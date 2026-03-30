# Coding agent backends

> See [Executors](executors.md) for when to choose a coding agent backend over a provider-backed executor.

The `claude-code-sdk`, `claude-code-cli`, and `codex` backends delegate Natural block execution to a coding agent CLI. All three implement the Pydantic AI `Model` protocol internally and expose Nighthawk tools to the CLI via an embedded MCP server. See [For coding agents](for-coding-agents.md) for a development guide targeting coding agents building Python projects with Nighthawk.

## Why coding agent backends

With coding agent backends, each Natural block becomes an autonomous agent execution -- the agent can read files, run commands, and invoke skills, constrained by Nighthawk's typed binding contract at the boundary. See [Philosophy](philosophy.md#execution-model) for the full positioning argument and [Executors](executors.md#capability-matrix) for capability and cost comparisons.

## Minimal configuration

Claude Code (SDK):
```py
configuration = nh.StepExecutorConfiguration(model="claude-code-sdk:default")
```

Claude Code (CLI):
```py
configuration = nh.StepExecutorConfiguration(model="claude-code-cli:default")
```

Codex:
```py
configuration = nh.StepExecutorConfiguration(model="codex:default")
```

The segment after `:` selects the model. Use `default` to let the backend choose its default model, or specify a model alias recognized by the backend CLI (e.g., `claude-code-sdk:sonnet`, `codex:gpt-5.4-mini`). Available aliases depend on the backend CLI version.

Backend-specific settings are configured via the `model_settings` field of `StepExecutorConfiguration`. Each backend provides a settings class (`ClaudeCodeSdkModelSettings`, `ClaudeCodeCliModelSettings`, `CodexModelSettings`) that can be passed directly — `StepExecutorConfiguration` auto-converts `BaseModel` instances to dicts internally.

## Shared capabilities

All three backends provide capabilities not available with Pydantic AI providers. See the [capability matrix](executors.md#capability-matrix) for a summary.

- **Skill execution:** The CLI loads and executes skills from its standard skill directories. See [Skills](#skills) below.
- **MCP tool exposure:** Nighthawk tools are exposed to the CLI via an embedded MCP server started automatically.
- **Callable discoverability:** Same rules as [Natural blocks](natural-blocks.md#functions-and-discoverability).
- **Project-scoped files:** The CLI loads its own project files (CLAUDE.md, AGENTS.md) from `working_directory`.

### Working directory

All three backends accept `working_directory` in their model settings. This absolute path determines:

- Where the CLI resolves project-scoped files (CLAUDE.md, AGENTS.md, skills, settings)
- The working directory for CLI execution

### Error handling

If a backend CLI process fails (e.g., crashes, times out, or returns an invalid response), Nighthawk surfaces the failure as an `ExecutionError`. See [Patterns](patterns.md#error-handling) for error handling patterns.

### Allowed tool names

Use `allowed_tool_names` to restrict which Nighthawk tools are exposed to the backend via MCP. When `None` (default), all registered tools are exposed.

```py
from nighthawk.backends.claude_code_sdk import ClaudeCodeSdkModelSettings

configuration = nh.StepExecutorConfiguration(
    model="claude-code-sdk:default",
    model_settings=ClaudeCodeSdkModelSettings(
        allowed_tool_names=("nh_eval", "nh_assign"),
    ),
)
```

### Settings comparison

All three backends share `working_directory` and `allowed_tool_names` (described above). The table below lists backend-specific fields only:

| Field | Claude Code CLI | Claude Code SDK | Codex |
|---|---|---|---|
| `permission_mode` | 4 modes or `None` | 4 modes or `None` | -- |
| `setting_sources` | `list[SettingSource] | None` | `list[SettingSource] | None` | -- |
| `max_turns` | `int | None` | `int | None` | -- |
| `executable` | `str`, default `"claude"` | -- | `str`, default `"codex"` |
| `max_budget_usd` | `float | None` | -- | -- |
| `claude_allowed_tool_names` | -- | `tuple[str, ...] | None` | -- |
| `sandbox_mode` | -- | -- | 3 modes or `None` |
| `model_reasoning_effort` | -- | -- | 5 levels or `None` |

See each backend section below for full field-level documentation.

## Claude Code (CLI)

The `claude-code-cli` backend invokes `claude -p` directly as a subprocess, without the Claude Agent SDK. It communicates via JSON output and exposes Nighthawk tools via an HTTP MCP server on a random local port.

### Installation

```bash
curl -fsSL https://claude.ai/install.sh | bash
claude auth login
pip install nighthawk-python[claude-code-cli]
```

The `claude` CLI must be installed separately (it is a system tool, not a Python package).

### Environment

- **Authentication:** Requires `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or an active Claude Code session.
- **CLI invocation:** Nighthawk invokes `claude -p --output-format json --no-session-persistence` as a subprocess. Additional flags (`--model`, `--system-prompt-file`, `--permission-mode`, `--max-turns`, `--max-budget-usd`, `--mcp-config`, `--json-schema`, `--allowedTools`) are appended based on `ClaudeCodeCliModelSettings`.
- **MCP tool exposure:** An HTTP MCP server is started in a background thread on a random local port. The Claude Code CLI connects via `--mcp-config`. Tool names are prefixed with `mcp__nighthawk__` in the Claude Code environment.
- **Working directory:** Passed as the `cwd` parameter to `create_subprocess_exec`.
- **Project-scoped files:** Resolved from `working_directory` following Claude Code's own rules (CLAUDE.md, .claude/CLAUDE.md, .claude/settings.json, .claude/skills/).

### Settings

```py
from nighthawk.backends.claude_code_cli import ClaudeCodeCliModelSettings

configuration = nh.StepExecutorConfiguration(
    model="claude-code-cli:sonnet",
    model_settings=ClaudeCodeCliModelSettings(
        permission_mode="bypassPermissions",
        setting_sources=["project"],
        max_turns=50,
        max_budget_usd=5.0,
        working_directory="/abs/path/to/project",
    ),
)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `working_directory` | `str` | `""` (CLI default) | Absolute path to the project directory |
| `allowed_tool_names` | `tuple[str, ...]` \| `None` | `None` | Nighthawk tool names exposed to the model |
| `permission_mode` | `"default"` \| `"acceptEdits"` \| `"plan"` \| `"bypassPermissions"` \| `None` | `None` | Claude Code permission mode. `None` delegates to the CLI default. |
| `setting_sources` | `list[SettingSource]` \| `None` | `None` | Setting source scopes to load |
| `max_turns` | `int` \| `None` | `None` | Maximum conversation turns |
| `executable` | `str` | `"claude"` | Path or name of the Claude Code CLI executable |
| `max_budget_usd` | `float` \| `None` | `None` | Maximum dollar amount to spend on API calls |

## Claude Code (SDK)

The `claude-code-sdk` backend uses the [Claude Agent SDK](https://docs.anthropic.com/en/docs/agent-sdk) to run Claude Code as a subprocess.

### Installation

```bash
pip install nighthawk-python[claude-code-sdk]
```

### Environment

- **Authentication:** Requires `ANTHROPIC_API_KEY`.
- **Execution:** Unlike the CLI backend, the SDK runs in-process via the Agent SDK -- there is no subprocess invocation or CLI flag configuration.
- **MCP tool exposure:** Nighthawk wraps tools as `SdkMcpTool` instances and exposes them via the Agent SDK's in-process MCP server (`create_sdk_mcp_server()`). Tool names are prefixed with `mcp__nighthawk__` in the Claude Code environment.
- **Working directory:** Passed as the `cwd` option to the Agent SDK.
- **Project-scoped files:** Resolved from `working_directory` following Claude Code's own rules (CLAUDE.md, .claude/CLAUDE.md, .claude/settings.json, .claude/skills/).

### Settings

```py
from nighthawk.backends.claude_code_sdk import ClaudeCodeSdkModelSettings

configuration = nh.StepExecutorConfiguration(
    model="claude-code-sdk:sonnet",
    model_settings=ClaudeCodeSdkModelSettings(
        permission_mode="bypassPermissions",
        setting_sources=["project"],
        claude_allowed_tool_names=("Skill", "Bash"),
        max_turns=50,
        working_directory="/abs/path/to/project",
    ),
)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `working_directory` | `str` | `""` (CLI default) | Absolute path to the project directory |
| `allowed_tool_names` | `tuple[str, ...]` \| `None` | `None` | Nighthawk tool names exposed to the model |
| `permission_mode` | `"default"` \| `"acceptEdits"` \| `"plan"` \| `"bypassPermissions"` \| `None` | `None` | Claude Code permission mode. `None` delegates to the SDK default. |
| `setting_sources` | `list[SettingSource]` \| `None` | `None` | Setting source scopes to load (`SettingSource` is `"user"`, `"project"`, or `"local"`) |
| `max_turns` | `int` \| `None` | `None` | Maximum conversation turns. `None` delegates to the SDK default. |
| `claude_allowed_tool_names` | `tuple[str, ...]` \| `None` | `None` | Additional Claude Code native tool names to allow (SDK only; CLI does not support this field) |

## Codex

The `codex` backend runs Codex CLI as a subprocess via `codex exec` and communicates via an HTTP MCP server started on a random local port.

### Installation

```bash
npm install -g @openai/codex
codex login
pip install nighthawk-python[codex]
```

The `codex` CLI must be installed separately (it is a system tool, not a Python package).

### Environment

- **Authentication:** Requires a valid Codex CLI authentication (e.g. `codex login`). For integration tests, set `CODEX_API_KEY`.
- **CLI invocation:** Nighthawk invokes `codex exec --experimental-json --skip-git-repo-check` as a subprocess. Additional flags (`--sandbox`, `--config`, `--output-schema`, `--cd`) are appended based on `CodexModelSettings`.
- **MCP tool exposure:** An HTTP MCP server is started in a background thread on a random local port. The Codex CLI connects to this server via `--config mcp_servers.nighthawk.url=<url>`. Tool names follow the `mcp_servers.nighthawk.*` namespace.
- **Working directory:** Passed to Codex CLI via `--cd`.
- **Project-scoped files:** Codex CLI loads its own instruction files (AGENTS.md) and skills from `.agents/skills/`. The loading rules are defined by Codex CLI, not by Nighthawk.

### Settings

```py
from nighthawk.backends.codex import CodexModelSettings

configuration = nh.StepExecutorConfiguration(
    model="codex:default",
    model_settings=CodexModelSettings(
        working_directory="/abs/path/to/project",
    ),
)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `working_directory` | `str` | `""` (CLI default) | Absolute path to the project directory |
| `allowed_tool_names` | `tuple[str, ...]` \| `None` | `None` | Nighthawk tool names exposed to the model |
| `executable` | `str` | `"codex"` | Path or name of the Codex CLI executable |
| `sandbox_mode` | `"read-only"` \| `"workspace-write"` \| `"danger-full-access"` \| `None` | `None` | Sandbox policy for CLI commands |
| `model_reasoning_effort` | `"minimal"` \| `"low"` \| `"medium"` \| `"high"` \| `"xhigh"` \| `None` | `None` | Reasoning effort level |

### Known issue: MCP tools + structured output

The OpenAI Responses API intermittently fails with "stream disconnected" errors when a request contains both MCP-sourced function tools and `text.format.json_schema` (structured output via `--output-schema`). This affects all Codex CLI versions tested (0.110.0 through 0.116.0). When Nighthawk exposes tools via MCP and the step executor requests structured output, the combination may trigger server-side errors in the upstream API.

**Current status:** awaiting an upstream fix in the Codex CLI or OpenAI Responses API. No workaround is applied on the Nighthawk side. Promptfoo evaluations for the `codex` provider may show intermittent errors for this reason.

## Skills

A **skill** is a reusable, project-scoped instruction set that a coding agent can discover and execute. Claude Code skills use `.claude/skills/` ([documentation](https://code.claude.com/docs/en/skills)); Codex skills use `.agents/skills/` ([documentation](https://developers.openai.com/codex/skills/)). Nighthawk does not define its own skill format -- it delegates to the backend CLI's native skill system.

All three backends can execute skills. A skill is a directory with a `SKILL.md` file that the CLI loads from its standard skill directories. You can share one skill definition across both backends using symlinks:

```text
project-root/
├── skills/
│   └── summarize-feedback/
│       └── SKILL.md
├── .claude/
│   └── skills -> ../skills
└── .agents/
    └── skills -> ../skills
```

Each backend reads skills from its own directory convention:

- **Claude Code** (SDK and CLI): `.claude/skills/`
- **Codex**: `.agents/skills/`

The symlink approach above lets a single skill definition serve both backends.

Skill configuration differs between backends:

- **Claude Code** (SDK and CLI): supports SKILL.md frontmatter fields such as `context`, `agent`, `allowed-tools`, and `disable-model-invocation`. See the [Claude Code skills documentation](https://code.claude.com/docs/en/skills) for available options.
- **Codex**: uses a separate `agents/openai.yaml` file for invocation policy and tool dependencies. See the [Codex skills documentation](https://developers.openai.com/codex/skills/) for available options.

Example `SKILL.md`:

```md
---
name: summarize-feedback
description: Summarize feedback using available helper functions.
---

Call `group_feedback_by_topic(feedback_items=<feedback_items>)`.
Use the returned groups to set <:summary_markdown> as exactly 3 bullet points.
```

Example Natural function that invokes the skill:

```py
import nighthawk as nh

@nh.natural_function
def summarize_feedback(feedback_items: list[str]) -> str:
    def group_feedback_by_topic(feedback_items: list[str]) -> dict[str, list[str]]:
        """Group feedback by topic using deterministic keyword rules."""
        topic_to_feedback_items: dict[str, list[str]] = {}
        for feedback_item in feedback_items:
            topic = "other"
            lowered_feedback_item = feedback_item.lower()
            if "slow" in lowered_feedback_item or "latency" in lowered_feedback_item:
                topic = "performance"
            elif "error" in lowered_feedback_item or "fail" in lowered_feedback_item:
                topic = "reliability"
            topic_to_feedback_items.setdefault(topic, []).append(feedback_item)
        return topic_to_feedback_items

    summary_markdown = ""
    """natural
    ---
    deny: [raise, return]
    ---
    Execute the `summarize-feedback` skill to set <:summary_markdown>.
    """
    return summary_markdown
```

Callable discoverability in skills follows the same rules as regular Natural functions. See [Natural blocks](natural-blocks.md#functions-and-discoverability).

## Troubleshooting

**`FileNotFoundError: claude` or `codex` not found**

The backend CLI must be installed separately. Claude Code CLI is a system tool (not a Python package); install it with `curl -fsSL https://claude.ai/install.sh | bash`. Codex CLI is also a system tool; install it with `npm install -g @openai/codex`.

**`ANTHROPIC_API_KEY` not set (Claude Code backends)**

Set `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` before running: `export ANTHROPIC_API_KEY=sk-ant-xxxxxxxxx`. Alternatively, authenticate via an active Claude Code session.

**`CODEX_API_KEY` not set or Codex login required**

Authenticate with `codex login`, or set `CODEX_API_KEY` for non-interactive use.

**MCP server connection failure**

Nighthawk starts an embedded MCP server on a random local port. Ensure no firewall rules block localhost connections. For `claude-code-cli` and `codex` backends, check that the CLI version supports MCP configuration.

**Codex structured output errors ("stream disconnected")**

The OpenAI Responses API intermittently fails when a request contains both MCP-sourced function tools and structured output (`--output-schema`). This affects Codex CLI versions 0.110.0 through 0.116.0. No workaround is available; awaiting an upstream fix. See [Known issue](#known-issue-mcp-tools-structured-output) above.
