# Governance & Policies

One of the primary value propositions of running Hermes through Omnigent
is the **governance layer** — stateful, contextual policies enforced at
the meta-harness level, not via prompts. A prompt-injected or misbehaving
Hermes agent **cannot bypass them**.

## How Policies Work

Omnigent intercepts every action (tool calls, LLM requests, file
operations) and evaluates contextual policies in real time:

| Decision | What Happens |
|---|---|
| **ALLOW** | The action proceeds |
| **ASK** | The action pauses until a human approves/rejects |
| **DENY** | The action is blocked with an error message |

Because `handles_tools_internally()=True`, Omnigent sees Hermes's tool
calls as **informational events** but can still enforce policies on the
outer agent-start and LLM-request boundaries.

## Policy Examples

### Cost Budget

Prevent runaway spending — warn at $1, pause at $10:

```yaml
policies:
  - name: cost_guard
    type: function
    function:
      path: omnigent.policies.builtins.cost.cost_budget
      arguments:
        ask_thresholds_usd: [1.0]
        max_cost_usd: 10.0
```

### Approval Gates

Require human approval for dangerous operations (git push after
package installs, destructive commands):

```yaml
policies:
  - name: approve_dangerous
    type: function
    function:
      path: omnigent.policies.builtins.security.approve_dangerous
      arguments:
        tool_patterns:
          - "git push"
          - "git push.*"
          - "rm -rf"
          - "docker.*--privileged"
```

### Rate Limiting

Limit how many tool calls Hermes can make per session:

```yaml
policies:
  - name: rate_limiter
    type: function
    function:
      path: omnigent.policies.builtins.rate.limit
      arguments:
        max_calls_per_minute: 30
        max_calls_per_session: 500
```

### Model Routing

Route trivial questions to cheaper models, reserve expensive models
for complex tasks:

```yaml
policies:
  - name: model_router
    type: function
    function:
      path: omnigent.policies.builtins.routing.model_router
      arguments:
        trivial_models: ["glm-5-turbo"]
        complex_models: ["claude-sonnet-4-6"]
        trivial_keywords: ["what is", "define", "list", "summarize"]
```

## Combining Policies

Policies are evaluated **in order**. The first to return a decision
(ALLOW/ASK/DENY) wins. No opinion (`None`) passes to the next.

```yaml
policies:
  # 1. Always block obviously dangerous patterns
  - name: block_dangerous
    type: function
    function:
      path: omnigent.policies.builtins.security.deny_dangerous

  # 2. Ask for approval on risky operations
  - name: approve_risky
    type: function
    function:
      path: omnigent.policies.builtins.security.approve_dangerous

  # 3. Enforce cost budget
  - name: cost_guard
    type: function
    function:
      path: omnigent.policies.builtins.cost.cost_budget
      arguments:
        max_cost_usd: 10.0

  # 4. Everything else: allow
```

## Enforcement Levels

| Level | Who Sets It | Scope |
|---|---|---|
| **Session** | End user | Current session only |
| **Agent config** | Developer | Every session using this agent |
| **Server-wide** | Admin | Every agent/session on the server |

## Hermes-Specific Considerations

Since Hermes has 70+ built-in tools and handles its own tool loop:

1. **Tool policies apply at the agent boundary** — Omnigent can gate
   `__agent_start` and LLM requests, but Hermes's internal tool calls
   are not individually interceptable (they execute inside the Hermes
   process).

2. **OS sandbox (Omnibox) wraps the entire Hermes process** — file
   system isolation, network allow-listing, and credential injection
   work at the kernel level regardless of which tools Hermes calls.

3. **Cost tracking works** — the `usage` dict from
   `AIAgent.run_conversation()` is forwarded to Omnigent's cost
   advisor via `TurnComplete.usage`.

```yaml
# Enable OS sandbox for unattended Hermes runs
os_env:
  type: caller_process
  cwd: .
  sandbox:
    write_paths: [.]
    allow_network:
      hosts: [api.openai.com, api.anthropic.com]
```
