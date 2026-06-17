# CLAUDE.md — Hermes Omnigent Harness

## Project Identity

**What:** Custom Omnigent harness adapter wrapping Hermes agent sessions.
**Status:** v0.2.0, 39/39 tests, CI green, published at [github.com/innerline/hermes-omnigent-harness](https://github.com/innerline/hermes-omnigent-harness)
**Purpose:** Gives Hermes three things it lacks — live session sharing, mobile access, and a governance layer.

## Architecture (3 components)

```
hermes_harness.py   → create_app() → ExecutorAdapter → FastAPI
hermes_executor.py  → HermesExecutor(Executor), wraps AIAgent.run_conversation()
_event_bridge.py    → HermesStreamBridge: sync callbacks → async ExecutorEvent stream
_register.py        → Auto-registration: patches 6 Omnigent locations idempotently
```

## Critical Design Rules

1. **`handles_tools_internally() = True`** — Hermes has its own 70+ tool loop. Omnigent passes through tool events; do NOT re-execute them.
2. **Library pattern, not subprocess** — Import `AIAgent` directly. No tmux.
3. **Thread pool bridge** — Hermes is sync, Omnigent is async. `run_conversation()` runs in `loop.run_in_executor()`, events drain from `asyncio.Queue` with 0.5s timeout.
4. **Conditional imports** — `hermes_executor.py` uses `try/except ImportError` so unit tests run without omnigent installed (CI). `_event_bridge.py` uses lazy `_import_executor_types()`.

## Environment Variables (HARNESS_HERMES_*)

| Var | Purpose | Default |
|---|---|---|
| `HARNESS_HERMES_MODEL` | Model identifier | Hermes config default |
| `HARNESS_HERMES_BASE_URL` | API/gateway base URL | Hermes config default |
| `HARNESS_HERMES_API_KEY` | API key | Hermes `~/.hermes/.env` |
| `HARNESS_HERMES_PROFILE` | Hermes profile name | Default profile |
| `HARNESS_HERMES_ENABLED_TOOLSETS` | Comma-separated toolsets | Hermes default |
| `HARNESS_HERMES_DISABLED_TOOLSETS` | Comma-separated toolsets | None |
| `HARNESS_HERMES_OS_ENV` | JSON-encoded OSEnvSpec | caller_process, no sandbox |
| `HARNESS_HERMES_MAX_TURNS` | Max agent iterations | Hermes default (200) |
| `HERMES_SOURCE_DIR` | Path to hermes-agent source | `~/.hermes/hermes-agent` |

## The 6 Omnigent Patch Points

The registration system (`_register.py` / `scripts/register_harness.py`) patches these:

1. `_HARNESS_MODULES` → `runtime/harnesses/__init__.py` (runtime registry)
2. `OMNIGENT_HARNESSES` → `spec/_omnigent_compat.py` (CLI allowlist)
3. `_OS_ENV_HARNESSES` → `cli.py` (OS env tool injection)
4. `_HARNESS_MODEL_ENV_KEY` → `runner/app.py` (`/model` override mapping)
5. `_build_spawn_env_from_spec` → `runner/app.py` (dispatch case)
6. `_build_hermes_spawn_env` → `runtime/workflow.py` (credential bridge function)

All patches are **idempotent** — safe to run multiple times.

## Key Paths

- Omnigent source: `/Users/brianoconnell/.local/share/uv/tools/omnigent/lib/python3.13/site-packages/omnigent/`
- Omnigent Python: `/Users/brianoconnell/.local/share/uv/tools/omnigent/bin/python`
- Hermes source: `~/.hermes/hermes-agent/`
- Pi harness reference: `omnigent/inner/pi_harness.py` + `omnigent/inner/pi_executor.py`
- Omnigent server: `http://localhost:6767`

## Testing

```bash
# Without omnigent (CI): 7 unit tests pass, 2 files skipped
python -m pytest tests/ -v

# With omnigent: 39 tests pass
/Users/brianoconnell/.local/share/uv/tools/omnigent/bin/python -m pytest tests/ -v
```

Install dev deps: `VIRTUAL_ENV="$(uv tool dir)/omnigent" uv pip install -e ".[dev]"`

## Lessons Learned (AI Layer)

These are friction points discovered during development. Follow them to avoid repeating mistakes:

### 1. Omnigent has NO plugin system for new harnesses
Every harness is hardcoded in 6 locations across the Omnigent codebase. You cannot register a harness via entry points alone — you must patch Omnigent's source files. The `.pth` auto-registration mechanism is our workaround.

### 2. The Omnigent server caches the allowlist at boot
After patching `OMNIGENT_HARNESSES`, you MUST restart the server:
```bash
omni stop && omni server start
```
Otherwise `omni run --harness hermes` fails with "Unsupported harness" even though the patch is in the file.

### 3. Hermes is synchronous; bridge it, don't fight it
`AIAgent.run_conversation()` blocks. Don't try to make it async. Use `loop.run_in_executor(None, sync_fn)` and an `asyncio.Queue` to bridge events. The 0.5s queue timeout is load-bearing — it lets us check interrupts and drain the live message queue between events.

### 4. Conditional imports are required for CI
Omnigent isn't on PyPI. CI can't `pip install omnigent`. Use `try/except ImportError` with `object` fallback for the `Executor` base class, and `pytest.importorskip("omnigent")` for tests that need real ExecutorEvent types. Without this, CI fails at import time.

### 5. Ruff line-length is 100 — watch long string literals
The `_register.py` file has long string literals for patching Omnigent source. These exceed 100 chars. Either add per-file ignores in `pyproject.toml` (`[tool.ruff.lint.per-file-ignores]`) or break them up.

### 6. `int | float` syntax needs Python 3.10+
The `isinstance(v, int | float)` syntax doesn't work on 3.9 (system Python). Use `isinstance(v, (int, float))` for compatibility. Our `requires-python = ">=3.11"` covers this, but the system Python for quick checks may be older.

### 7. `from __future__ import annotations` must be FIRST
After `pytest.importorskip()`, ruff complains about import ordering (E402). Add `# noqa: E402` to the import that follows `importorskip`, and configure per-file-ignores for test files.

### 8. Hermes resolves its own credentials
Don't try to pass Omnigent's credential system into Hermes. Hermes has its own `~/.hermes/config.yaml` + `.env` with all providers configured. The credential bridge only needs to pass the **model name** — Hermes handles the rest.

### 9. The Pi harness is the reference, not Claude/Codex
Claude and Codex harnesses spawn terminal subprocesses via tmux. That's complex and unnecessary for Hermes. The Pi harness (`pi_harness.py`) imports its executor as a library — that's the pattern we follow. Pi is headless and model-agnostic, just like Hermes.

### 10. `handles_tools_internally()` changes the policy model
When True, Omnigent cannot intercept individual tool calls inside Hermes's process. Policies still work at the agent-start boundary, LLM-request boundary, and OS-sandbox level — but NOT at the per-tool level. Document this limitation for users expecting per-tool governance.
