# Coding agent backends

The `claude-code-sdk`, `claude-code-cli`, and `codex` backends delegate Natural block execution to a coding agent CLI. All three implement the Pydantic AI `Model` protocol internally and expose Nighthawk tools to the CLI via an embedded MCP server. See [Providers](providers.md) for the full provider landscape and capability matrix. See [For coding agents](for-coding-agents.md) for a development guide targeting coding agents working on Nighthawk projects.

Minimal configuration:

```python
from nighthawk.configuration import StepExecutorConfiguration

# Claude Code (SDK)
configuration = StepExecutorConfiguration(model="claude-code-sdk:default")

# Claude Code (CLI)
configuration = StepExecutorConfiguration(model="claude-code-cli:default")

# Codex
configuration = StepExecutorConfiguration(model="codex:default")
```

The segment after `:` selects the model. Use `default` to let the backend choose its default model, or specify a model alias recognized by the backend CLI (e.g., `claude-code-sdk:sonnet`, `codex:o3-pro`). Available aliases depend on the backend CLI version.

Backend-specific settings are configured via the `model_settings` field of `StepExecutorConfiguration`. Each backend provides a settings class (`ClaudeCodeSdkModelSettings`, `ClaudeCodeCliModelSettings`, `CodexModelSettings`) that can be passed directly — `StepExecutorConfiguration` auto-converts `BaseModel` instances to dicts internally.

## Shared capabilities

All three backends provide features that are not available with Pydantic AI providers:

| Capability | How it works |
|---|---|
| Skill execution | The CLI loads and executes skills from its standard skill directories |
| MCP tool exposure | Nighthawk tools are exposed to the CLI via an embedded MCP server |
| Callable discoverability | Same rules as [Tutorial Section 3](tutorial.md#3-functions-and-discoverability) |
| Project-scoped files | The CLI loads its own project files from `working_directory` |

### Working directory

All three backends accept `working_directory` in their model settings. This absolute path determines:

- Where the CLI resolves project-scoped files (CLAUDE.md, AGENTS.md, skills, settings)
- The working directory for CLI execution

### Error handling

If a backend CLI process fails (e.g., crashes, times out, or returns an invalid response), Nighthawk surfaces the failure as an `ExecutionError`. See [Tutorial Section 4](tutorial.md#4-control-flow-and-error-handling) for error handling patterns.

## Claude Code (SDK)

The `claude-code-sdk` backend uses the [Claude Agent SDK](https://docs.anthropic.com/en/docs/agent-sdk) to run Claude Code as a subprocess.

### Installation

```bash
pip install nighthawk-python[claude-code-sdk]
```

### Environment

- **Authentication:** Requires `ANTHROPIC_API_KEY` or an active Claude Code session.
- **MCP tool exposure:** Nighthawk wraps tools as `SdkMcpTool` instances and exposes them via the Agent SDK's in-process MCP server (`create_sdk_mcp_server()`). Tool names are prefixed with `mcp__nighthawk__` in the Claude Code environment.
- **Working directory:** Passed as the `cwd` option to the Agent SDK.
- **Project-scoped files:** Resolved from `working_directory` following Claude Code's own rules (CLAUDE.md, .claude/CLAUDE.md, .claude/settings.json, .claude/skills/).

### Settings

```python
from nighthawk.backends.claude_code_sdk import ClaudeCodeSdkModelSettings

configuration = StepExecutorConfiguration(
    model="claude-code-sdk:sonnet",
    model_settings=ClaudeCodeSdkModelSettings(
        permission_mode="bypassPermissions",
        setting_sources=["project"],
        claude_allowed_tool_names=("Skill", "Bash"),
        claude_max_turns=50,
        working_directory="/abs/path/to/project",
    ),
)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `permission_mode` | `"default"` \| `"acceptEdits"` \| `"plan"` \| `"bypassPermissions"` | `"default"` | Claude Code permission mode |
| `setting_sources` | `list[SettingSource]` \| `None` | `None` | Setting source scopes to load (`SettingSource` is `"user"`, `"project"`, or `"local"`) |
| `allowed_tool_names` | `tuple[str, ...]` \| `None` | `None` | Nighthawk tool names exposed to the model |
| `claude_allowed_tool_names` | `tuple[str, ...]` \| `None` | `None` | Additional Claude Code native tool names to allow (SDK only; CLI does not support this field) |
| `claude_max_turns` | `int` | `50` | Maximum conversation turns |
| `working_directory` | `str` | `""` | Absolute path to the project directory |

## Claude Code (CLI)

The `claude-code-cli` backend invokes `claude -p` directly as a subprocess, without the Claude Agent SDK. It communicates via JSON output and exposes Nighthawk tools via an HTTP MCP server on a random local port.

### Installation

```bash
pip install nighthawk-python[claude-code-cli]
```

The `claude` CLI must be installed separately (it is a system tool, not a Python package).

### Environment

- **Authentication:** Requires `ANTHROPIC_API_KEY` or an active Claude Code session.
- **CLI invocation:** Nighthawk invokes `claude -p --output-format json --no-session-persistence` as a subprocess. Additional flags (`--model`, `--system-prompt-file`, `--permission-mode`, `--max-turns`, `--max-budget-usd`, `--mcp-config`, `--json-schema`, `--allowedTools`) are appended based on `ClaudeCodeCliModelSettings`.
- **MCP tool exposure:** An HTTP MCP server is started in a background thread on a random local port. The Claude Code CLI connects via `--mcp-config`. Tool names are prefixed with `mcp__nighthawk__` in the Claude Code environment.
- **Working directory:** Passed as the `cwd` parameter to `create_subprocess_exec`.
- **Project-scoped files:** Resolved from `working_directory` following Claude Code's own rules (CLAUDE.md, .claude/CLAUDE.md, .claude/settings.json, .claude/skills/).

### Settings

```python
from nighthawk.backends.claude_code_cli import ClaudeCodeCliModelSettings

configuration = StepExecutorConfiguration(
    model="claude-code-cli:sonnet",
    model_settings=ClaudeCodeCliModelSettings(
        permission_mode="bypassPermissions",
        setting_sources=["project"],
        claude_max_turns=50,
        max_budget_usd=5.0,
        working_directory="/abs/path/to/project",
    ),
)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `allowed_tool_names` | `tuple[str, ...]` \| `None` | `None` | Nighthawk tool names exposed to the model |
| `claude_executable` | `str` | `"claude"` | Path or name of the Claude Code CLI executable |
| `claude_max_turns` | `int` \| `None` | `None` | Maximum conversation turns |
| `max_budget_usd` | `float` \| `None` | `None` | Maximum dollar amount to spend on API calls |
| `permission_mode` | `"default"` \| `"acceptEdits"` \| `"plan"` \| `"bypassPermissions"` \| `None` | `None` | Claude Code permission mode. `None` delegates to the CLI default. |
| `setting_sources` | `list[SettingSource]` \| `None` | `None` | Setting source scopes to load |
| `working_directory` | `str` | `""` | Absolute path to the project directory |

## Codex

The `codex` backend runs Codex CLI as a subprocess via `codex exec` and communicates via an HTTP MCP server started on a random local port.

### Installation

```bash
pip install nighthawk-python[codex]
```

### Environment

- **Authentication:** Requires a valid Codex CLI authentication (e.g. `codex login`). For integration tests, set `CODEX_API_KEY`.
- **CLI invocation:** Nighthawk invokes `codex exec --experimental-json --skip-git-repo-check` as a subprocess. Additional flags (`--sandbox`, `--config`, `--output-schema`, `--cd`) are appended based on `CodexModelSettings`.
- **MCP tool exposure:** An HTTP MCP server is started in a background thread on a random local port. The Codex CLI connects to this server via `--config mcp_servers.nighthawk.url=<url>`. Tool names follow the `mcp_servers.nighthawk.*` namespace.
- **Working directory:** Passed to Codex CLI via `--cd`.
- **Project-scoped files:** Codex CLI loads its own instruction files (AGENTS.md) and skills from `.agents/skills/`. The loading rules are defined by Codex CLI, not by Nighthawk.

### Settings

```python
from nighthawk.backends.codex import CodexModelSettings

configuration = StepExecutorConfiguration(
    model="codex:default",
    model_settings=CodexModelSettings(
        working_directory="/abs/path/to/project",
    ),
)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `allowed_tool_names` | `tuple[str, ...]` \| `None` | `None` | Nighthawk tool names exposed to the model |
| `codex_executable` | `str` | `"codex"` | Path or name of the Codex CLI executable |
| `model_reasoning_effort` | `"minimal"` \| `"low"` \| `"medium"` \| `"high"` \| `"xhigh"` \| `None` | `None` | Reasoning effort level |
| `sandbox_mode` | `"read-only"` \| `"workspace-write"` \| `"danger-full-access"` \| `None` | `None` | Sandbox policy for CLI commands |
| `working_directory` | `str` | `""` | Absolute path to the project directory |

## Skills

All three backends can execute skills. A skill is a directory with a `SKILL.md` file that the CLI loads from its standard skill directories. You can share one skill definition across both backends using symlinks:

```text
project-root/
|-- skills/
|   `-- summarize-feedback/
|       `-- SKILL.md
|-- .claude/
|   `-- skills -> ../skills
`-- .agents/
    `-- skills -> ../skills
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

```python
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
    deny: [pass, raise]
    ---
    Execute the `summarize-feedback` skill.
    """
    return summary_markdown
```

Callable discoverability in skills follows the same rules as regular Natural functions. See [Tutorial Section 3](tutorial.md#3-functions-and-discoverability).

### Integration tests

- `tests/integration/test_claude_code_sdk_integration.py::test_claude_skill_calc`
- `tests/integration/test_claude_code_cli_integration.py::test_claude_code_cli_skill_calc`
- `tests/integration/test_codex_integration.py::test_codex_skill_calc`
