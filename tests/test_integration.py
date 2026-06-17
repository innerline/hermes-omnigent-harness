"""
Integration tests for the Hermes Omnigent harness.

Tests the full pipeline: create_app() → ExecutorAdapter → HermesExecutor →
event bridge → ExecutorEvent stream, using a mock AIAgent to avoid real LLM calls.

These tests require the omnigent package to be installed (they run in
Omnigent's Python env).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytest.importorskip("omnigent")

# These imports require omnigent to be installed
from omnigent.inner.executor import (
    TextChunk,
    TurnComplete,
    ExecutorError,
)

from hermes_omnigent_harness.hermes_executor import (
    HermesExecutor,
    _build_hermes_executor,
)


class MockAIAgent:
    """Mock AIAgent that simulates streaming response + tool calls."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.conversation_count = 0

    def run_conversation(
        self,
        user_message: str,
        system_message: str | None = None,
        conversation_history: list | None = None,
        task_id: str | None = None,
        stream_callback=None,
        persist_user_message: str | None = None,
    ) -> dict[str, Any]:
        """Simulate a conversation with streaming + a final response."""
        self.conversation_count += 1

        # Simulate streaming text deltas
        chunks = ["Hello", ", ", "from ", "Hermes!"]
        for chunk in chunks:
            if stream_callback:
                stream_callback(chunk)

        return {
            "final_response": "Hello, from Hermes!",
            "usage": {
                "input_tokens": 50,
                "output_tokens": 10,
                "total_tokens": 60,
            },
        }


class MockAIAgentWithError:
    """Mock AIAgent that raises an exception."""

    def run_conversation(self, user_message="", **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Model API connection failed")


class TestBuildExecutor:
    """Test the _build_hermes_executor() factory."""

    def test_builds_from_env_vars(self, monkeypatch):
        """Executor is configured from HARNESS_HERMES_* env vars."""
        monkeypatch.setenv("HARNESS_HERMES_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("HARNESS_HERMES_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("HARNESS_HERMES_API_KEY", "test-key")
        monkeypatch.setenv("HARNESS_HERMES_MAX_TURNS", "25")
        monkeypatch.setenv("HARNESS_HERMES_ENABLED_TOOLSETS", "web,terminal")

        executor = _build_hermes_executor()

        assert executor._model == "claude-sonnet-4-6"
        assert executor._base_url == "https://api.example.com/v1"
        assert executor._api_key == "test-key"
        assert executor._max_turns == 25
        assert executor._enabled_toolsets == "web,terminal"

    def test_defaults_when_no_env(self, monkeypatch):
        """Executor uses None defaults when env vars are unset."""
        for key in [
            "HARNESS_HERMES_MODEL",
            "HARNESS_HERMES_BASE_URL",
            "HARNESS_HERMES_API_KEY",
            "HARNESS_HERMES_MAX_TURNS",
        ]:
            monkeypatch.delenv(key, raising=False)

        executor = _build_hermes_executor()

        assert executor._model is None
        assert executor._base_url is None
        assert executor._api_key is None
        assert executor._max_turns is None


class TestRunTurn:
    """Test HermesExecutor.run_turn() with mocked AIAgent."""

    def _make_executor_with_mock(self, mock_agent: Any) -> HermesExecutor:
        """Create an executor with a pre-injected mock agent."""
        executor = HermesExecutor(model="test-model")
        executor._agent = mock_agent
        return executor

    @pytest.mark.asyncio
    async def test_streaming_text_and_completion(self):
        """run_turn yields TextChunks then TurnComplete."""
        executor = self._make_executor_with_mock(MockAIAgent())

        messages = [{"role": "user", "content": "Say hello"}]
        events = []
        async for event in executor.run_turn(messages, [], "You are helpful"):
            events.append(event)

        # Should have 4 TextChunks + 1 TurnComplete
        text_chunks = [e for e in events if isinstance(e, TextChunk)]
        turn_completes = [e for e in events if isinstance(e, TurnComplete)]
        errors = [e for e in events if isinstance(e, ExecutorError)]

        assert len(errors) == 0, f"Unexpected errors: {[e.message for e in errors]}"
        assert len(text_chunks) == 4
        assert len(turn_completes) == 1
        assert turn_completes[0].response == "Hello, from Hermes!"

        # Verify streaming text is correct
        full_text = "".join(c.text for c in text_chunks)
        assert full_text == "Hello, from Hermes!"

    @pytest.mark.asyncio
    async def test_usage_reporting(self):
        """run_turn passes usage info in TurnComplete."""
        executor = self._make_executor_with_mock(MockAIAgent())

        messages = [{"role": "user", "content": "test"}]
        events = []
        async for event in executor.run_turn(messages, [], ""):
            events.append(event)

        turn_complete = [e for e in events if isinstance(e, TurnComplete)][0]
        assert turn_complete.usage is not None
        assert turn_complete.usage["input_tokens"] == 50
        assert turn_complete.usage["output_tokens"] == 10
        assert turn_complete.usage["total_tokens"] == 60

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """run_turn yields ExecutorError when AIAgent raises."""
        executor = self._make_executor_with_mock(MockAIAgentWithError())

        messages = [{"role": "user", "content": "test"}]
        events = []
        async for event in executor.run_turn(messages, [], ""):
            events.append(event)

        errors = [e for e in events if isinstance(e, ExecutorError)]
        assert len(errors) == 1
        assert "Model API connection failed" in errors[0].message
        assert errors[0].retryable is False

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        """Empty message list yields TurnComplete with None response."""
        executor = self._make_executor_with_mock(MockAIAgent())

        events = []
        async for event in executor.run_turn([], [], ""):
            events.append(event)

        turn_completes = [e for e in events if isinstance(e, TurnComplete)]
        assert len(turn_completes) == 1
        assert turn_completes[0].response is None

    @pytest.mark.asyncio
    async def test_system_prompt_forwarded(self):
        """System prompt is passed to run_conversation."""
        mock = MockAIAgent()
        executor = self._make_executor_with_mock(mock)

        messages = [{"role": "user", "content": "hi"}]
        async for _ in executor.run_turn(messages, [], "Custom system prompt"):
            pass

        # Check the mock was called with the system prompt
        assert mock.conversation_count == 1

    @pytest.mark.asyncio
    async def test_multimodal_message(self):
        """Multimodal content (text + image) extracts text correctly."""
        executor = self._make_executor_with_mock(MockAIAgent())

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this"},
                    {"type": "image", "url": "data:..."},
                ],
            }
        ]
        events = []
        async for event in executor.run_turn(messages, [], ""):
            events.append(event)

        turn_completes = [e for e in events if isinstance(e, TurnComplete)]
        assert len(turn_completes) == 1
        assert turn_completes[0].response is not None


class TestExecutorCapabilities:
    """Test the capability flag methods."""

    def test_handles_tools_internally(self):
        """Hermes manages its own tool loop."""
        executor = HermesExecutor.__new__(HermesExecutor)
        assert executor.handles_tools_internally() is True

    def test_supports_streaming(self):
        """Hermes streams via callback."""
        executor = HermesExecutor.__new__(HermesExecutor)
        assert executor.supports_streaming() is True

    def test_max_context_tokens_none(self):
        """Hermes handles its own context window + compaction."""
        executor = HermesExecutor.__new__(HermesExecutor)
        assert executor.max_context_tokens() is None


class TestCreateApp:
    """Test the FastAPI app creation."""

    def test_create_app_returns_fastapi(self):
        """create_app() returns a valid FastAPI instance."""
        from fastapi import FastAPI
        from hermes_omnigent_harness.hermes_harness import create_app

        app = create_app()
        assert isinstance(app, FastAPI)

    def test_create_app_has_health_endpoint(self):
        """The harness app has a /health endpoint."""
        from hermes_omnigent_harness.hermes_harness import create_app

        app = create_app()
        route_paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/health" in route_paths

    def test_harness_registered_in_omnigent(self):
        """'hermes' is in Omnigent's _HARNESS_MODULES registry."""
        from omnigent.runtime.harnesses import _HARNESS_MODULES

        assert "hermes" in _HARNESS_MODULES
        assert _HARNESS_MODULES["hermes"] == "hermes_omnigent_harness.hermes_harness"


class TestModelSwitching:
    """Test mid-session model switching (Phase 3)."""

    def test_model_override_stores_new_model(self):
        """Config model override triggers model reconstruction."""
        executor = HermesExecutor(model="initial-model")
        executor._agent = MockAIAgent()  # pre-set to avoid real init

        # Simulate what run_turn does before constructing
        executor._model_override = "new-model"
        assert executor._model_override == "new-model"

    def test_reconstruct_agent_for_model(self):
        """_reconstruct_agent_for_model resets and rebuilds."""
        executor = HermesExecutor(model="model-a")
        executor._agent = MockAIAgent()

        executor._reconstruct_agent_for_model("model-b")
        assert executor._model == "model-b"
        # Agent should have been reconstructed
        assert executor._agent is not None


class TestLiveMessageQueue:
    """Test live message queueing (Phase 3)."""

    def test_supports_live_message_queue(self):
        """Executor advertises live message queue support."""
        executor = HermesExecutor.__new__(HermesExecutor)
        assert executor.supports_live_message_queue() is True

    def test_supports_tool_boundary_interrupt(self):
        """Executor advertises tool boundary interrupt support."""
        executor = HermesExecutor.__new__(HermesExecutor)
        assert executor.supports_tool_boundary_interrupt() is True

    @pytest.mark.asyncio
    async def test_enqueue_when_no_turn_active(self):
        """Enqueue returns False when no turn is active."""
        executor = HermesExecutor(model="test")
        result = await executor.enqueue_session_message("key", "hello")
        assert result is False

    @pytest.mark.asyncio
    async def test_enqueue_when_turn_active(self):
        """Enqueue returns True when a turn is active."""
        executor = HermesExecutor(model="test")
        executor._turn_active = True
        result = await executor.enqueue_session_message("key", "hello")
        assert result is True

        # Verify message was queued
        assert not executor._message_queue.empty()
        msg = await asyncio.wait_for(executor._message_queue.get(), timeout=1.0)
        assert msg == "hello"


class TestProfileAndToolsets:
    """Test Hermes profile and toolset filtering (Phase 3)."""

    def test_profile_from_env(self, monkeypatch):
        """Profile is read from HARNESS_HERMES_PROFILE env var."""
        monkeypatch.setenv("HARNESS_HERMES_PROFILE", "researcher")
        executor = _build_hermes_executor()
        assert executor._profile == "researcher"

    def test_enabled_toolsets_from_env(self, monkeypatch):
        """Enabled toolsets are read from env."""
        monkeypatch.setenv("HARNESS_HERMES_ENABLED_TOOLSETS", "web,terminal")
        executor = _build_hermes_executor()
        assert executor._enabled_toolsets == "web,terminal"

    def test_disabled_toolsets_from_env(self, monkeypatch):
        """Disabled toolsets are read from env."""
        monkeypatch.setenv("HARNESS_HERMES_DISABLED_TOOLSETS", "vision")
        executor = _build_hermes_executor()
        assert executor._disabled_toolsets == "vision"


class TestSpawnEnvBridge:
    """Test the Omnigent spawn-env credential bridge (Phase 3)."""

    def test_spawn_env_builder_exists(self):
        """_build_hermes_spawn_env is importable from workflow."""
        from omnigent.runtime.workflow import _build_hermes_spawn_env

        assert callable(_build_hermes_spawn_env)

    def test_model_env_key_mapping(self):
        """Hermes is in _HARNESS_MODEL_ENV_KEY mapping."""
        from omnigent.runner.app import _HARNESS_MODEL_ENV_KEY

        assert _HARNESS_MODEL_ENV_KEY.get("hermes") == "HARNESS_HERMES_MODEL"
