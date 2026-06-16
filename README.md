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
git clone <repo-url>
cd hermes-omnigent-harness
uv pip install -e .
```

### Register the Harness

Add to Omnigent's harness registry or use the entry point:

```bash
omni run my-agent/ --harness hermes
```

### Define an Agent

```yaml
# my-agent/config.yaml
spec_version: 1
name: my_hermes_agent
prompt: You are a helpful coding assistant.
executor:
  type: omnigent
  config:
    harness: hermes
  model: claude-sonnet-4-6
tools:
  filesystem:
    type: mcp
    command: npx
    args: ["@modelcontextprotocol/server-filesystem", "."]
```

## Architecture

This project implements the Omnigent harness contract:

| Component | File | Responsibility |
|---|---|---|
| **Harness module** | `hermes_harness.py` | Exports `create_app() -> FastAPI` |
| **Executor** | `hermes_executor.py` | Subclasses `Executor`, wraps `AIAgent.run_conversation()` |
| **Event bridge** | `_event_bridge.py` | Translates Hermes streaming callbacks → `ExecutorEvent` stream |

### Why Hermes as an Omnigent Harness?

Hermes is uniquely suited:
- **`handles_tools_internally() = True`** — Hermes has its own tool loop (70+ tools), so Omnigent passes through tool events instead of re-executing
- **Model-agnostic** — works with any gateway provider, like the Pi harness
- **Rich built-in features** — memory, skills, MCP support, session persistence
- **Clean programmatic API** — `AIAgent.chat()` and `run_conversation()` with streaming callbacks

## License

MIT
