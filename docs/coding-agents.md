# Coding agents

The `claude-code` and `codex` backends delegate Natural block execution to a coding agent CLI. Both implement the Pydantic AI `Model` protocol internally and expose Nighthawk tools to the CLI via an embedded MCP server. See [Providers](providers.md) for the full provider landscape and capability matrix.

Minimal configuration:

```python
from nighthawk.configuration import StepExecutorConfiguration

# Claude Code
configuration = StepExecutorConfiguration(model="claude-code:default")

# Codex
configuration = StepExecutorConfiguration(model="codex:default")
```

## Shared capabilities

Both backends provide features that are not available with Pydantic AI providers:

| Capability | How it works |
|---|---|
| Skill execution | The CLI loads and executes skills from its standard skill directories |
| MCP tool exposure | Nighthawk tools are exposed to the CLI via an embedded MCP server |
| Callable discoverability | Same rules as [Tutorial Section 3](tutorial.md#3-functions-and-discoverability) |
| Project-scoped files | The CLI loads its own project files from `working_directory` |

### Working directory

Both backends accept `working_directory` in their model settings. This absolute path determines:

- Where the CLI resolves project-scoped files (CLAUDE.md, AGENTS.md, skills, settings)
- The working directory for CLI execution

## Claude Code

The `claude-code` backend uses the [Claude Agent SDK](https://docs.anthropic.com/en/docs/agent-sdk) to run Claude Code as a subprocess.

### Installation

```bash
pip install nighthawk[claude-code]
```

### Environment

- **Authentication:** Requires `ANTHROPIC_API_KEY` or an active Claude Code session.
- **MCP tool exposure:** Nighthawk wraps tools as `SdkMcpTool` instances and exposes them via the Agent SDK's in-process MCP server (`create_sdk_mcp_server()`). Tool names are prefixed with `mcp__nighthawk__` in the Claude Code environment.
- **Working directory:** Passed as the `cwd` option to the Agent SDK.
- **Project-scoped files:** Resolved from `working_directory` following Claude Code's own rules (CLAUDE.md, .claude/CLAUDE.md, .claude/settings.json, .claude/skills/).

### Settings

```python
from nighthawk.backends.claude_code import ClaudeCodeModelSettings

configuration = StepExecutorConfiguration(
    model="claude-code:sonnet",
    model_settings=ClaudeCodeModelSettings(
        permission_mode="bypassPermissions",
        setting_sources=["project"],
        claude_allowed_tool_names=("Skill", "Bash"),
        claude_max_turns=50,
        working_directory="/abs/path/to/project",
    ).model_dump(),
)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `permission_mode` | `"default"` \| `"acceptEdits"` \| `"plan"` \| `"bypassPermissions"` | `"default"` | Claude Code permission mode |
| `setting_sources` | `list[SettingSource]` \| `None` | `None` | Setting source scopes to load (`SettingSource` is `"user"`, `"project"`, or `"local"`) |
| `allowed_tool_names` | `tuple[str, ...]` \| `None` | `None` | Nighthawk tool names exposed to the model |
| `claude_allowed_tool_names` | `tuple[str, ...]` \| `None` | `None` | Additional Claude Code native tool names to allow |
| `claude_max_turns` | `int` | `50` | Maximum conversation turns |
| `working_directory` | `str` | `""` | Absolute path to the project directory |

## Codex

The `codex` backend runs Codex CLI as a subprocess via `codex exec` and communicates via an HTTP MCP server started on a random local port.

### Installation

```bash
pip install nighthawk[codex]
```

### Environment

- **Authentication:** Requires a valid Codex CLI authentication (e.g. `codex login`). For integration tests, set `CODEX_API_KEY`.
- **CLI invocation:** Nighthawk invokes `codex exec --experimental-json --skip-git-repo-check` as a subprocess. Additional flags (`--sandbox`, `--config`, `--output-schema`, `--cd`) are appended based on `CodexModelSettings`.
- **MCP tool exposure:** An HTTP MCP server is started in a background thread on a random local port. The Codex CLI connects to this server via `--config mcp_servers.nighthawk.url=<url>`. Tool names follow the `mcp_servers.nighthawk.*` namespace.
- **Working directory:** Passed to Codex CLI via `--cd`.
- **Project-scoped files:** Instruction-file and skill-loading behavior is defined by Codex CLI, not by Nighthawk.

### Settings

```python
from nighthawk.backends.codex import CodexModelSettings

configuration = StepExecutorConfiguration(
    model="codex:default",
    model_settings=CodexModelSettings(
        working_directory="/abs/path/to/project",
    ).model_dump(),
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

Both backends can execute skills. A skill is a directory with a `SKILL.md` file that the CLI loads from its standard skill directories. You can share one skill definition across both backends using symlinks:

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

- `tests/integration/test_claude_code_integration.py::test_claude_skill_calc`
- `tests/integration/test_codex_integration.py::test_codex_skill_calc`
