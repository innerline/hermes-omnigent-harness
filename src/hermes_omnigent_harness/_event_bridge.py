"""
Event bridge: translate Hermes streaming callbacks → Omnigent ExecutorEvents.

Hermes communicates streaming output via a ``stream_callback(delta_text)``
callable and returns a structured dict from ``run_conversation()``. This
module bridges that callback-based model into the async-generator model
Omnigent's ``Executor.run_turn()`` expects.

Strategy:
    1. An ``asyncio.Queue`` is the conduit between the sync callback and
       the async generator.
    2. The ``HermesStreamBridge.stream_callback`` is passed to Hermes as
       ``stream_callback=`` — it enqueues ``TextChunk`` events.
    3. ``events()`` is an async generator that the executor's ``run_turn``
       drains, yielding ``ExecutorEvent`` instances to Omnigent.
    4. When ``run_conversation()`` returns, the final result dict is
       translated into a ``TurnComplete`` event and enqueued.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from omnigent.inner.executor import (
        ExecutorEvent,
    )

logger = logging.getLogger(__name__)


def _import_executor_types():
    """Lazily import Omnigent executor types at runtime.

    Allows this module to be imported without omnigent installed —
    the types are only needed when actually creating events.
    """
    from omnigent.inner.executor import (
        ExecutorEvent,
        TextChunk,
        TurnComplete,
    )
    return ExecutorEvent, TextChunk, TurnComplete

# Sentinel placed on the queue to signal "conversation finished, drain
# remaining events then stop."
_DONE = object()


class HermesStreamBridge:
    """Bridges Hermes callback-based streaming into an async event stream.

    Usage::

        bridge = HermesStreamBridge()
        result = agent.run_conversation(
            user_message,
            stream_callback=bridge.stream_callback,
        )
        bridge.finish(result)

        async for event in bridge.events():
            yield event
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[ExecutorEvent | object] = asyncio.Queue()
        self._finished = False

    def stream_callback(self, delta: str) -> None:
        """Callback for Hermes streaming text deltas.

        Called synchronously by Hermes during ``run_conversation()``.
        Enqueues a :class:`TextChunk` for the async generator to drain.

        :param delta: Incremental text delta from the model.
        """
        if delta:
            _, TextChunk, _ = _import_executor_types()
            try:
                self._queue.put_nowait(TextChunk(text=delta))
            except asyncio.QueueFull:
                logger.warning("Event bridge queue full — dropping text delta")

    def tool_call_started(self, name: str, args: dict[str, Any] | None = None) -> None:
        """Notify the bridge that a tool call has started.

        Hermes handles tools internally, so these are informational events
        for Omnigent to display (not re-execute).

        :param name: The tool's registered name.
        :param args: The tool call arguments.
        """
        from omnigent.inner.executor import ToolCallRequest

        try:
            self._queue.put_nowait(ToolCallRequest(name=name, args=args or {}))
        except asyncio.QueueFull:
            logger.warning("Event bridge queue full — dropping tool call event")

    def tool_call_finished(
        self,
        name: str,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        """Notify the bridge that a tool call has completed.

        :param name: The tool's registered name.
        :param result: The tool's return value.
        :param error: Error message if the call failed.
        """
        from omnigent.inner.executor import (
            ToolCallComplete,
            ToolCallStatus,
        )

        status = ToolCallStatus.ERROR if error else ToolCallStatus.SUCCESS
        try:
            self._queue.put_nowait(
                ToolCallComplete(
                    name=name,
                    status=status,
                    result=result,
                    error=error,
                )
            )
        except asyncio.QueueFull:
            logger.warning("Event bridge queue full — dropping tool complete event")

    def finish(self, result: dict[str, Any]) -> None:
        """Signal that ``run_conversation()`` has returned.

        Translates the Hermes result dict into a :class:`TurnComplete`
        event and marks the bridge as finished so ``events()`` stops.

        :param result: The dict returned by
            ``AIAgent.run_conversation()``. Expected keys:
            ``final_response`` (str), ``usage`` (optional dict).
        """
        if self._finished:
            return
        self._finished = True

        response = (
            result.get("final_response") if isinstance(result, dict) else str(result)
        )
        usage = None
        if isinstance(result, dict) and isinstance(result.get("usage"), dict):
            raw_usage = result["usage"]
            usage = {
                k: v
                for k, v in raw_usage.items()
                if k
                in (
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "prompt_tokens",
                    "completion_tokens",
                )
                and isinstance(v, (int, float))
            }

        try:
            _, _, TurnComplete = _import_executor_types()
            self._queue.put_nowait(TurnComplete(response=response, usage=usage or None))
        except asyncio.QueueFull:
            logger.error("Event bridge queue full — cannot enqueue TurnComplete")
        finally:
            self._queue.put_nowait(_DONE)

    def finish_with_error(self, message: str, retryable: bool = False) -> None:
        """Signal an error during conversation execution.

        :param message: Human-readable error description.
        :param retryable: Whether this error might succeed on retry.
        """
        from omnigent.inner.executor import ExecutorError

        if self._finished:
            return
        self._finished = True

        try:
            self._queue.put_nowait(ExecutorError(message=message, retryable=retryable))
        except asyncio.QueueFull:
            logger.error("Event bridge queue full — cannot enqueue ExecutorError")
        finally:
            self._queue.put_nowait(_DONE)

    async def events(self) -> asyncio.Queue[ExecutorEvent | object]:
        """Return the underlying queue for the executor to drain.

        The executor reads from this queue in its ``run_turn`` async
        generator. The sentinel ``_DONE`` signals end-of-stream.
        """
        return self._queue

    @staticmethod
    def is_done(item: object) -> bool:
        """Check whether a dequeued item is the end sentinel."""
        return item is _DONE
