# Hermes Omnigent Harness — Implementation Plan

## Status: ✅ Phase 3 Complete — 39/39 tests passing

## What's Built

### Core Files
- [x] `src/hermes_omnigent_harness/hermes_harness.py` — `create_app()` FastAPI entry point via `ExecutorAdapter`
- [x] `src/hermes_omnigent_harness/hermes_executor.py` — `HermesExecutor(Executor)` wrapping `AIAgent.run_conversation()`
- [x] `src/hermes_omnigent_harness/_event_bridge.py` — `HermesStreamBridge` translating sync callbacks → async ExecutorEvent stream

### Tests
- [x] `tests/test_event_bridge.py` — 8 tests covering streaming, completion, errors, usage extraction, idempotency
- [x] `tests/test_executor.py` — 7 tests for message extraction (simple, multimodal, edge cases)

### Supporting
- [x] `scripts/register_harness.py` — registers `"hermes"` in Omnigent's `_HARNESS_MODULES`
- [x] `examples/hermes-agent/config.yaml` — example agent spec with cost + security policies
- [x] `CLAUDE.md` — technical context for AI agents
- [x] `README.md` — architecture overview + quick start

## Architecture

```
Omnigent Server (policies, collaboration, mobile)
         ↕ WebSocket tunnel
Omnigent Runner (host)
  └─ hermes_harness.py: create_app() → ExecutorAdapter
      └─ hermes_executor.py: HermesExecutor
          └─ _event_bridge.py: HermesStreamBridge
              └─ AIAgent.run_conversation() [in thread pool]
```

Key decisions:
- **Library pattern** (not subprocess/tmux) — imports `AIAgent` directly
- **`handles_tools_internally()=True`** — Hermes has its own 70+ tool loop
- **Thread pool bridge** — Hermes is sync, Omnigent expects async events
- **Mirrors `pi_harness.py`** pattern exactly

## What Remains

### Phase 2: Integration Testing ✅
- [x] Install package in Omnigent's Python env and run `create_app()`
- [x] Test end-to-end with `omni run --harness hermes` — passes CLI + server validation
- [x] Verify streaming, tool events, and session persistence work through the Omnigent UI
- [x] Harness registered in all 6 Omnigent patch points

### Phase 3: Advanced Features ✅
- [x] Mid-session model switching (`/model` command support) — `_reconstruct_agent_for_model()`
- [x] `enqueue_session_message()` for live queueing during a turn
- [x] Hermes profile support (`HARNESS_HERMES_PROFILE` env var)
- [x] Toolset filtering from agent YAML (`HARNESS_HERMES_ENABLED/DISABLED_TOOLSETS`)
- [x] Credential bridge — `_build_hermes_spawn_env()` in workflow.py, model env key mapping
- [x] `supports_live_message_queue()` + `supports_tool_boundary_interrupt()` capability flags

### Phase 4: Polish
- [ ] Package on PyPI (`pip install hermes-omnigent-harness`)
- [ ] Entry point registration (no manual `__init__.py` edit needed)
- [ ] Documentation for governance policy examples
- [ ] CI pipeline
