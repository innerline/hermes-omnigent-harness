# hermes-omnigent-harness

A custom **Omnigent harness** that wraps **Hermes agent sessions**, giving Hermes the three capabilities it currently lacks:

- **Live session sharing** — URL-based collaboration, co-drive, fork conversations
- **Mobile/multi-device access** — same session on terminal, web, desktop, phone
- **Governance layer** — stateful contextual policies (cost budgets, approval gates, risk scoring)

## How It Works

```
┌─────────────────────────────────────────────────┐
│                  OMNIGENT SERVER                  │
│  Policies · Session History · Collaboration      │
│  Auth/SSO · Cost Tracking · Mobile/Web UI        │
└──────────────────┬──────────────────────────────┘
                   │ WebSocket tunnel
                   ▼
┌─────────────────────────────────────────────────┐
│                   RUNNER (HOST)                   │
│  ┌─────────────────────────────────────────────┐ │
│  │     hermes_omnigent_harness                  │ │
│  │  ┌──────────────┐    ┌────────────────────┐ │ │
│  │  │ HermesHarness│───▶│  HermesExecutor     │ │ │
│  │  │ (FastAPI app)│    │  (Executor subclass)│ │ │
│  │  └──────────────┘    └───────┬────────────┘ │ │
│  │                              │               │ │
│  │                    ┌─────────▼──────────┐    │ │
│  │                    │  AIAgent (Hermes)   │    │ │
│  │                    │  Tools · Memory     │    │ │
│  │                    │  Skills · MCP       │    │ │
│  │                    └────────────────────┘    │ │
│  └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites

- [Omnigent](https://omnigent.ai/) (`omni` CLI v0.1.1+)
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) (v0.16+)
- Python 3.11–3.13

### Install

```bash
# Clone and install into Omnigent's Python environment
git clone https://github.com/innerline/hermes-omnigent-harness.git
cd hermes-omnigent-harness

# Install into the same env that runs Omnigent
VIRTUAL_ENV="$(uv tool dir)/omnigent" uv pip install -e .
```

### Register the Harness

```bash
# Automatic — patches all 6 Omnigent integration points
hermes-register

# Or equivalently:
python scripts/register_harness.py
```

The registration patches Omnigent's harness registry, CLI allowlist,
OS-env tool injection, model override mapping, spawn-env dispatch, and
credential bridge. It's **idempotent** — safe to run multiple times.

### Run Hermes Through Omnigent

```bash
# Direct harness launch
omni run --harness hermes -p "Write a Python function to reverse a linked list"

# With a custom agent spec (policies, tools, model)
omni run my-agent/ --harness hermes
```

## Agent Configuration

```yaml
# my-agent/config.yaml
spec_version: 1
name: my_hermes_agent
description: Hermes agent with governance and collaboration

prompt: >
  You are Hermes, a self-improving AI agent. You have access to tools,
  memory, and skills. Help the user accomplish their tasks.

executor:
  type: omnigent
  config:
    harness: hermes
    # Optional: override Hermes profile (multi-profile support)
    # profile: researcher
    # Optional: filter toolsets
    # enabled_toolsets: web,terminal
    # disabled_toolsets: vision
  model: claude-sonnet-4-6

# Contextual policies — enforced by Omnigent, NOT bypassable by Hermes
policies:
  - name: cost_guard
    type: function
    function:
      path: omnigent.policies.builtins.cost.cost_budget
      arguments:
        ask_thresholds_usd: [1.0]
        max_cost_usd: 10.0

  - name: approve_dangerous
    type: function
    function:
      path: omnigent.policies.builtins.security.approve_dangerous
      arguments:
        tool_patterns: ["git push", "rm -rf"]

# OS sandbox for unattended runs
os_env:
  type: caller_process
  cwd: .
  sandbox:
    write_paths: [.]
    allow_network: true
```

## Architecture

| Component | File | Responsibility |
|---|---|---|
| **Harness module** | `hermes_harness.py` | Exports `create_app() -> FastAPI` via `ExecutorAdapter` |
| **Executor** | `hermes_executor.py` | Subclasses `Executor`, wraps `AIAgent.run_conversation()` |
| **Event bridge** | `_event_bridge.py` | Translates Hermes streaming callbacks → `ExecutorEvent` stream |
| **Auto-registration** | `_register.py` | Patches Omnigent at install/startup time |

### Key Design Decisions

- **Library pattern** (not subprocess/tmux) — imports `AIAgent` directly, like the Pi harness
- **`handles_tools_internally() = True`** — Hermes has its own 70+ tool loop; Omnigent passes through tool events
- **Thread pool bridge** — Hermes is synchronous; Omnigent expects async events
- **Auto-registration via `.pth`** — harness self-registers on Python startup, no manual patching

### Why Hermes as an Omnigent Harness?

Hermes is uniquely suited:
- **Self-improving** — creates and improves skills from experience
- **Model-agnostic** — works with any gateway provider
- **Rich built-in features** — memory, skills, MCP support, session persistence
- **Clean programmatic API** — `AIAgent.chat()` and `run_conversation()` with streaming callbacks

## Features

| Feature | Status |
|---|---|
| Core harness (create_app, executor, event bridge) | ✅ |
| Streaming text + usage reporting | ✅ |
| Tool event pass-through | ✅ |
| Mid-session model switching (`/model`) | ✅ |
| Live message queueing | ✅ |
| Hermes profile support | ✅ |
| Toolset filtering from agent YAML | ✅ |
| Credential bridge (Omnigent → Hermes) | ✅ |
| Auto-registration (`.pth` + `hermes-register`) | ✅ |
| Governance policies (cost, security, routing) | ✅ |
| OS sandbox (Omnibox) integration | ✅ |
| PyPI package | ✅ |

## Testing

```bash
# Run in Omnigent's Python environment
VIRTUAL_ENV="$(uv tool dir)/omnigent" uv pip install -e ".[dev]"
VIRTUAL_ENV="$(uv tool dir)/omnigent" python -m pytest tests/ -v
```

39 tests covering: event bridge, executor, message extraction, model
switching, live queueing, profiles, toolsets, spawn-env bridge, FastAPI
app creation, and harness registration.

## Documentation

- [Governance & Policies](docs/governance.md) — cost budgets, approval gates, rate limiting, model routing
- [CLAUDE.md](CLAUDE.md) — technical context for AI agents

## License

MIT
