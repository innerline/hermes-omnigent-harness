# Hermes Omnigent Harness — Project Context

## What This Project Is

A custom **Omnigent harness adapter** that wraps **Hermes agent sessions**. This makes Hermes a first-class runtime in Omnigent alongside Claude Code, Codex, and Pi — giving Hermes live session sharing, mobile access, and a governance layer.

## Key Technical Decisions

### Library Pattern (not subprocess)
We wrap `AIAgent` as a Python library, not via subprocess/tmux. This mirrors the Pi harness (`pi_harness.py`) rather than the Claude/Codex native harnesses. Hermes has a clean programmatic API:
- `AIAgent(base_url, model)` — constructor
- `agent.run_conversation(user_message, stream_callback)` — full conversation loop
- `stream_callback(delta_text)` — streaming text deltas

### Executor Interface
The Omnigent `Executor` base class requires implementing:
```python
async def run_turn(self, messages, tools, system_prompt, config) -> AsyncIterator[ExecutorEvent]
```
Events yielded: `TextChunk`, `ToolCallRequest`, `TurnComplete`, `ExecutorError`

### Hermes-Specific Capabilities
- `handles_tools_internally()` → **True** (Hermes has its own 70+ tool loop)
- `supports_streaming()` → True
- Session persistence via `~/.hermes/sessions/`

## File Layout
```
src/hermes_omnigent_harness/
├── __init__.py
├── hermes_harness.py      # create_app() — FastAPI entry point
├── hermes_executor.py     # Executor subclass wrapping AIAgent
└── _event_bridge.py       # Hermes callbacks → ExecutorEvent stream
```

## Reference Harness
The **Pi harness** at `omnigent/inner/pi_harness.py` is the closest reference — also headless and model-agnostic. The pattern is:
1. `create_app()` → `ExecutorAdapter(executor_factory=_build_executor)` → `adapter.build()`
2. `_build_executor()` reads env vars, constructs the executor
3. `ExecutorAdapter` wraps the executor and serves the harness API

## Omnigent Harness Registration
Add `"hermes": "hermes_omnigent_harness.hermes_harness"` to `_HARNESS_MODULES` in `omnigent/runtime/harnesses/__init__.py`, or register via entry point.

## Dependencies
- Omnigent v0.1.1+ (harness SDK — Executor base class, ExecutorAdapter)
- Hermes Agent v0.16+ (AIAgent programmatic API)
- FastAPI (harness API subset)

## Environment Variables
Config follows the `HARNESS_HERMES_*` convention (mirrors `HARNESS_PI_*`):
- `HARNESS_HERMES_MODEL` — model identifier
- `HARNESS_HERMES_BASE_URL` — gateway/API base URL
- `HARNESS_HERMES_CWD` — working directory
- `HARNESS_HERMES_PROFILE` — Hermes profile name
- `HARNESS_HERMES_OS_ENV` — JSON-encoded OSEnvSpec
