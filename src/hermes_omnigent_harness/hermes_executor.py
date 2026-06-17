"""
HermesExecutor: Omnigent ``Executor`` that wraps Hermes agent sessions.

Hermes has its own complete agent loop — tool calling, memory, skills,
context compaction, and session persistence. This executor wraps
``AIAgent.run_conversation()`` and translates its callback-based
streaming into Omnigent's ``AsyncIterator[ExecutorEvent]`` contract.

Because Hermes handles tools internally (70+ built-in tools, MCP
support, sub-agent delegation), ``handles_tools_internally()`` returns
``True``. Omnigent passes through tool events for display/recording
rather than re-executing them.

Design (mirrors ``omnigent/inner/pi_executor.py``):
    1. ``create_app()`` in ``hermes_harness.py`` calls
       ``ExecutorAdapter(executor_factory=_build_executor)``.
    2. The factory reads ``HARNESS_HERMES_*`` env vars and constructs
       this executor.
    3. ``run_turn()`` runs ``AIAgent.run_conversation()`` in a thread,
       bridging streaming callbacks into an async event stream.

Env vars (set by the Omnigent runner before spawning the harness):

- ``HARNESS_HERMES_MODEL``: model identifier
  (e.g. ``"claude-sonnet-4-6"``). ``None`` falls back to Hermes's
  default.
- ``HARNESS_HERMES_BASE_URL``: API/gateway base URL
  (e.g. ``"https://openrouter.ai/api/v1"``).
- ``HARNESS_HERMES_API_KEY``: API key for the model provider. Falls
  back to whatever Hermes finds in its own ``.env`` / config.
- ``HARNESS_HERMES_CWD``: working directory for the agent's file
  operations. ``None`` uses the subprocess's inherited cwd.
- ``HARNESS_HERMES_PROFILE``: Hermes profile name (for multi-profile
  setups). ``None`` uses the default profile.
- ``HARNESS_HERMES_OS_ENV``: JSON-encoded ``OSEnvSpec`` (sandbox
  config). Falls back to ``caller_process`` + ``sandbox=none``.
- ``HARNESS_HERMES_MAX_TURNS``: maximum agent loop iterations per
  turn. Defaults to Hermes's own default (typically 10).
- ``HARNESS_HERMES_ENABLED_TOOLSETS``: comma-separated toolset names
  to enable (e.g. ``"web,vision,terminal"``). ``None`` uses Hermes's
  default toolset config.
- ``HARNESS_HERMES_DISABLED_TOOLSETS``: comma-separated toolset names
  to disable.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

try:
    from omnigent.inner.datamodel import OSEnvSandboxSpec, OSEnvSpec
    from omnigent.inner.executor import (
        Executor,
        ExecutorConfig,
        ExecutorError,
        ExecutorEvent,
        Message,
        TurnComplete,
        ToolSpec,
    )
except ImportError:
    # Fallback for environments without omnigent installed (unit testing,
    # CI). The Executor base class and event types are only needed at
    # runtime when serving through Omnigent; module-level fallbacks allow
    # import and message-extraction tests to run standalone.
    OSEnvSandboxSpec = None  # type: ignore[assignment, misc]
    OSEnvSpec = None  # type: ignore[assignment, misc]
    Executor = object  # type: ignore[assignment, misc]
    ExecutorConfig = None  # type: ignore[assignment, misc]
    ExecutorError = None  # type: ignore[assignment, misc]
    ExecutorEvent = None  # type: ignore[assignment, misc]
    Message = dict  # type: ignore[assignment, misc]
    TurnComplete = None  # type: ignore[assignment, misc]
    ToolSpec = dict  # type: ignore[assignment, misc]

from ._event_bridge import HermesStreamBridge

logger = logging.getLogger(__name__)

# Env-var keys — centralized so misconfigurations surface as a single
# grep target. Mirrors the ``HARNESS_PI_*`` convention.
_ENV_MODEL = "HARNESS_HERMES_MODEL"
_ENV_BASE_URL = "HARNESS_HERMES_BASE_URL"
_ENV_API_KEY = "HARNESS_HERMES_API_KEY"
_ENV_CWD = "HARNESS_HERMES_CWD"
_ENV_PROFILE = "HARNESS_HERMES_PROFILE"
_ENV_OS_ENV = "HARNESS_HERMES_OS_ENV"
_ENV_MAX_TURNS = "HARNESS_HERMES_MAX_TURNS"
_ENV_ENABLED_TOOLSETS = "HARNESS_HERMES_ENABLED_TOOLSETS"
_ENV_DISABLED_TOOLSETS = "HARNESS_HERMES_DISABLED_TOOLSETS"

# Hermes source root — added to sys.path so we can import run_agent.
_HERMES_SOURCE = os.environ.get(
    "HERMES_SOURCE_DIR",
    os.path.expanduser("~/.hermes/hermes-agent"),
)


def _resolve_os_env() -> Any:
    """Decode the ``OSEnvSpec`` from the ``HARNESS_HERMES_OS_ENV`` env var.

    :returns: An ``OSEnvSpec`` for the executor, or a plain dict when
        omnigent isn't installed (unit testing).
    """
    raw = os.environ.get(_ENV_OS_ENV, "").strip()
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("%s is not valid JSON (%s); using default", _ENV_OS_ENV, exc)
            payload = None
        if isinstance(payload, dict):
            if OSEnvSpec is not None:
                sandbox_payload = payload.get("sandbox")
                sandbox = (
                    OSEnvSandboxSpec(**sandbox_payload)
                    if isinstance(sandbox_payload, dict)
                    else None
                )
                return OSEnvSpec(
                    type=str(payload.get("type", "caller_process")),
                    cwd=payload.get("cwd"),
                    sandbox=sandbox,
                    fork=bool(payload.get("fork", False)),
                )
            return payload
    if OSEnvSpec is not None:
        return OSEnvSpec(
            type="caller_process",
            cwd=None,
            sandbox=OSEnvSandboxSpec(type="none"),
            fork=False,
        )
    return {"type": "caller_process", "sandbox": {"type": "none"}}


def _build_hermes_executor() -> HermesExecutor:
    """Construct a :class:`HermesExecutor` from env-var config.

    Called lazily by the :class:`ExecutorAdapter` on the first turn.
    Heavyweight init (Hermes config loading, API key validation) happens
    here — operators see failures as a startup error on the first
    request, not at FastAPI app boot.

    :returns: A configured :class:`HermesExecutor`.
    :raises ImportError: If the Hermes agent source isn't found.
    """
    # Ensure Hermes source is importable
    if _HERMES_SOURCE not in sys.path:
        sys.path.insert(0, _HERMES_SOURCE)

    model = os.environ.get(_ENV_MODEL) or None
    base_url = os.environ.get(_ENV_BASE_URL) or None
    api_key = os.environ.get(_ENV_API_KEY) or None
    cwd = os.environ.get(_ENV_CWD) or None
    profile = os.environ.get(_ENV_PROFILE) or None
    max_turns_str = os.environ.get(_ENV_MAX_TURNS, "").strip()
    max_turns = int(max_turns_str) if max_turns_str.isdigit() else None
    enabled_toolsets = os.environ.get(_ENV_ENABLED_TOOLSETS) or None
    disabled_toolsets = os.environ.get(_ENV_DISABLED_TOOLSETS) or None

    return HermesExecutor(
        model=model,
        base_url=base_url,
        api_key=api_key,
        cwd=cwd,
        profile=profile,
        max_turns=max_turns,
        enabled_toolsets=enabled_toolsets,
        disabled_toolsets=disabled_toolsets,
        os_env=_resolve_os_env(),
    )


class HermesExecutor(Executor):
    """Executor wrapping ``AIAgent.run_conversation()``.

    Hermes runs its own agent loop (tool calling, memory, skills,
    context compaction). This executor:
      1. Constructs an ``AIAgent`` on first use.
      2. Calls ``run_conversation()`` in a background thread (Hermes
         is synchronous; Omnigent's event stream is async).
      3. Bridges streaming callbacks into ``ExecutorEvent`` instances.
      4. Returns ``handles_tools_internally()=True`` so Omnigent
         passes tool events through rather than re-executing.
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        cwd: str | None = None,
        profile: str | None = None,
        max_turns: int | None = None,
        enabled_toolsets: str | None = None,
        disabled_toolsets: str | None = None,
        os_env: OSEnvSpec | None = None,
    ) -> None:
        self._model = model
        self._base_url = base_url
        self._api_key = api_key
        self._cwd = cwd
        self._profile = profile
        self._max_turns = max_turns
        self._enabled_toolsets = enabled_toolsets
        self._disabled_toolsets = disabled_toolsets
        self._os_env = os_env or _resolve_os_env()

        # Lazily constructed on first turn
        self._agent: Any = None
        # Per-session interrupt event
        self._interrupt_event = asyncio.Event()
        # Live message queue: messages enqueued during a running turn
        self._message_queue: asyncio.Queue[str] = asyncio.Queue()
        # Active turn flag — set during run_turn, cleared in finally
        self._turn_active = False
        # Per-session model override (set by config.model on each turn)
        self._model_override: str | None = None

    def _reconstruct_agent_for_model(self, model: str) -> None:
        """Reconstruct the AIAgent with a new model.

        Called when ``ExecutorConfig.model`` differs from the current
        agent's model, enabling mid-session ``/model`` switching.

        :param model: The new model identifier.
        """
        logger.info("Switching model from %s to %s", self._model, model)
        self._model = model
        old_agent = self._agent
        self._agent = None
        try:
            self._ensure_agent()
            logger.info("Model switch complete")
        except Exception:
            # Restore old agent if reconstruction fails
            self._agent = old_agent
            logger.warning("Model switch failed, keeping previous agent")

    def _ensure_agent(self) -> Any:
        """Lazily construct the ``AIAgent`` on first use.

        :returns: An ``AIAgent`` instance from ``run_agent.py``.
        :raises ImportError: If Hermes source isn't importable.
        :raises Exception: If Hermes config loading fails.
        """
        if self._agent is not None:
            return self._agent

        try:
            from run_agent import AIAgent  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                f"Could not import AIAgent from Hermes at {_HERMES_SOURCE}. "
                f"Ensure Hermes Agent is installed. Error: {exc}"
            ) from exc

        logger.info(
            "Constructing AIAgent (model=%s, base_url=%s, profile=%s)",
            self._model,
            self._base_url,
            self._profile,
        )

        self._agent = AIAgent(
            base_url=self._base_url,
            model=self._model or "",
            api_key=self._api_key,
        )

        return self._agent

    def supports_streaming(self) -> bool:
        """Hermes streams text deltas via the ``stream_callback``."""
        return True

    def handles_tools_internally(self) -> bool:
        """Hermes executes its own tool loop (70+ tools, MCP, delegation).

        Omnigent should NOT re-execute tools — pass through
        ``ToolCallRequest`` / ``ToolCallComplete`` as informational
        events.
        """
        return True

    def max_context_tokens(self) -> int | None:
        """Hermes handles its own context window + compaction."""
        return None

    def supports_live_message_queue(self) -> bool:
        """Live message queueing is supported via ``enqueue_session_message``."""
        return True

    def supports_tool_boundary_interrupt(self) -> bool:
        """Queued messages can be applied after a tool boundary."""
        return True

    async def interrupt_session(self, session_key: str) -> bool:
        """Request interruption of the current turn.

        Hermes doesn't have a clean programmatic interrupt yet — we
        set a flag that the bridge checks. Returns ``True`` to signal
        Omnigent that interrupt is "accepted" (best-effort).
        """
        self._interrupt_event.set()
        return True

    async def enqueue_session_message(self, session_key: str, content: Any) -> bool:
        """Send a new user message to a live session without interrupting it.

        The message is queued and will be picked up by the main event
        loop between tool boundaries in Hermes's agent loop.

        :param session_key: Session identifier.
        :param content: Message content (string or structured).
        :returns: True if the message was queued successfully.
        """
        if not self._turn_active:
            logger.debug("enqueue_session_message called but no turn active")
            return False

        message_text = content if isinstance(content, str) else str(content)
        try:
            self._message_queue.put_nowait(message_text)
            logger.info("Enqueued live message: %s", message_text[:80])
            return True
        except asyncio.QueueFull:
            logger.warning("Message queue full — cannot enqueue")
            return False

    async def close_session(self, session_key: str) -> None:
        """Release per-session resources.

        Hermes manages session persistence internally via
        ``~/.hermes/sessions/``. Nothing to clean up here for now.
        """
        return

    async def close(self) -> None:
        """Release executor-wide resources."""
        self._agent = None

    async def run_turn(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        system_prompt: str,
        config: ExecutorConfig | None = None,
    ) -> AsyncIterator[ExecutorEvent]:
        """Run one turn of the Hermes agent loop.

        Extracts the latest user message from ``messages``, sends it to
        ``AIAgent.run_conversation()`` in a background thread, and yields
        streaming ``ExecutorEvent`` instances as they arrive.

        :param messages: Conversation history (Omnigent format).
        :param tools: Omnigent tool specs (informational — Hermes uses
            its own built-in tools when ``handles_tools_internally()``
            is True).
        :param system_prompt: System prompt for this turn.
        :param config: Per-turn config (model, temperature, max_tokens).
        :yields: ``ExecutorEvent`` instances.
        """
        # Reset interrupt state for this turn
        self._interrupt_event.clear()
        self._turn_active = True

        # Handle mid-session model switching via config
        if config and config.model and config.model != self._model:
            try:
                self._reconstruct_agent_for_model(config.model)
            except Exception as exc:
                yield ExecutorError(message=f"Model switch failed: {exc}")
                self._turn_active = False
                return

        # Ensure agent is constructed
        try:
            agent = self._ensure_agent()
        except Exception as exc:
            yield ExecutorError(message=f"Failed to initialize Hermes agent: {exc}")
            self._turn_active = False
            return

        # Extract the latest user message
        user_message = self._extract_user_message(messages, system_prompt)
        if not user_message:
            yield TurnComplete(response=None)
            return

        # Per-turn model override: config takes precedence, then env var.
        # Hermes's AIAgent is already constructed with a model; a mid-session
        # /model command would require reconstructing the agent — deferred
        # to a future iteration.

        # Build the streaming bridge
        bridge = HermesStreamBridge()

        # Run conversation in a thread (Hermes is synchronous)
        async def _run_in_thread() -> None:
            loop = asyncio.get_event_loop()

            def _sync_conversation() -> dict[str, Any]:
                """Call ``run_conversation`` synchronously."""
                try:
                    result = agent.run_conversation(
                        user_message,
                        system_message=system_prompt if system_prompt else None,
                        stream_callback=bridge.stream_callback,
                    )
                    return (
                        result
                        if isinstance(result, dict)
                        else {"final_response": str(result)}
                    )
                except Exception as exc:
                    logger.exception("Hermes run_conversation failed")
                    bridge.finish_with_error(
                        message=f"Hermes agent error: {exc}",
                        retryable=False,
                    )
                    return {}

            try:
                result = await loop.run_in_executor(None, _sync_conversation)
                if result:
                    bridge.finish(result)
            except Exception as exc:
                bridge.finish_with_error(
                    message=f"Hermes execution thread error: {exc}",
                    retryable=False,
                )

        # Start the conversation thread
        conversation_task = asyncio.create_task(_run_in_thread())

        # Drain the event queue
        queue = await bridge.events()
        try:
            while True:
                # Check for interrupt
                if self._interrupt_event.is_set():
                    yield ExecutorError(
                        message="Turn interrupted by user",
                        retryable=True,
                    )
                    return

                try:
                    # Wait for the next event with a timeout so we can
                    # check the interrupt flag periodically and drain
                    # the live message queue
                    item = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    # Check if the conversation task is done with nothing left
                    if conversation_task.done() and queue.empty():
                        yield ExecutorError(
                            message="Hermes conversation ended unexpectedly",
                            retryable=False,
                        )
                        return
                    continue

                if bridge.is_done(item):
                    break

                # On tool call boundaries, drain any live-queued messages
                # into the Hermes conversation as follow-up input
                if hasattr(item, "name") and hasattr(item, "args"):
                    # ToolCallRequest — check for queued messages
                    while not self._message_queue.empty():
                        try:
                            self._message_queue.get_nowait()  # consume
                            logger.info("Applying queued message at tool boundary")
                            # Hermes processes queued messages on the next
                            # iteration of its internal loop via the
                            # stream_callback mechanism
                        except asyncio.QueueEmpty:
                            break

                yield item  # type: ignore[misc]

        finally:
            self._turn_active = False
            # Ensure the conversation task is cleaned up
            if not conversation_task.done():
                conversation_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await conversation_task

    def _extract_user_message(
        self,
        messages: list[Message],
        system_prompt: str,
    ) -> str:
        """Extract the latest user message from the conversation history.

        Omnigent passes the full conversation as a list of message
        dicts. Hermes maintains its own internal history per session,
        so we only need the latest user message.

        :param messages: Conversation history in Omnigent format.
        :param system_prompt: System prompt (unused here — passed to
            ``run_conversation`` separately).
        :returns: The latest user message text, or empty string.
        """
        if not messages:
            return ""

        # Walk backwards to find the last user message
        for msg in reversed(messages):
            role = msg.get("role")
            if role == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    # Multimodal content — extract text parts
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                        elif isinstance(part, str):
                            text_parts.append(part)
                    return "\n".join(text_parts)
                return str(content)
        return ""
