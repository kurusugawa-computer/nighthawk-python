---
paths:
  - "docs/**/*.md"
---

# Documentation rules

## File roles and boundaries

Each file has a distinct audience and scope. Content belongs in exactly one file; cross-reference rather than duplicate. Exception: `for-coding-agents.md` is standalone and condenses (distills) content from other files into actionable rules. This is distillation, not duplication.

| File | Audience | Role | Scope boundary |
|---|---|---|---|
| `index.md` | First-time visitors | Project overview, motivation, workflow styles | What Nighthawk is and why. No API details, no how-to. |
| `quickstart.md` | New users | Shortest path to running a Natural block | Setup, first example, backends table, credentials, troubleshooting. No deep explanations. |
| `tutorial.md` | Users learning the system | Build understanding from first principles | Bindings, functions and discoverability, control flow, composition, configuration, guidelines. Assumes quickstart is done. |
| `design.md` | Implementors and advanced users | Canonical specification (target behavior) | Full technical detail: syntax rules, state layers, prompt rendering, tool contracts, outcome schema, frontmatter. |
| `providers.md` | Users choosing and configuring models | Provider selection, Pydantic AI setup, custom backends | Provider categories, capability matrix, model identifiers, Pydantic AI model settings, step executor protocols. No coding-agent-backend-specific content. |
| `coding-agent-backends.md` | Users of Claude Code or Codex backends | Coding agent backend configuration and features | Backend-specific settings, skills, MCP tool exposure, working directory, project-scoped files. |
| `for-coding-agents.md` | Coding agents (LLMs) working on Nighthawk projects | Condensed development knowledge base | Nighthawk mental model, Natural block writing, binding function design, control flow, composition, testing, common mistakes. Not a human tutorial; an LLM reference. |
| `api.md` | Developers using the library | Auto-generated API reference (mkdocstrings) | Public API surface only. Content comes from source docstrings; do not hand-edit. |
| `roadmap.md` | Contributors and planners | Future directions | Ideas and desired directions only. Must not restate what is already implemented. |

## What goes where (decision guide)

- **Syntax rule or runtime contract?** -> `design.md`
- **How to use a feature with examples?** -> `tutorial.md`
- **Provider selection, Pydantic AI settings, or custom step executor?** -> `providers.md`
- **Coding agent backend settings, skills, MCP, or working directory?** -> `coding-agent-backends.md`
- **Backend-agnostic concept that applies across all providers?** -> `tutorial.md` (or `design.md` for strict contracts)
- **First-time setup or "just make it work"?** -> `quickstart.md`
- **Credential or authentication setup?** -> `quickstart.md` (Pydantic AI providers), `coding-agent-backends.md` (coding agent backends)
- **Error types or exception hierarchy?** -> `design.md` (specification), `tutorial.md` (practical usage with examples)
- **Not yet implemented?** -> `roadmap.md`
- **Public API signature or docstring?** -> `api.md` (edit the source docstring, not api.md)
- **Knowledge a coding agent needs to develop Nighthawk code?** -> `for-coding-agents.md`

## Writing guidelines for docs/

### General

- Cross-reference with relative links (e.g., `[Section 5](tutorial.md#5-cross-block-composition)`) instead of duplicating content. Exception: `for-coding-agents.md` uses absolute URLs based on `site_url` from `mkdocs.yml` (see [for-coding-agents.md specifics](#for-coding-agentsmd-specifics)).
- When tutorial.md and design.md cover the same concept, tutorial.md shows the "what and how" with examples; design.md specifies the "exact rules and edge cases".
- Keep code examples self-contained: a reader should understand the example without reading surrounding prose.
- Built-in tool names (`nh_eval`, `nh_exec`, `nh_assign`) are implementation details. Only `design.md` may expose them. All other files describe behavior instead (e.g., "the LLM can set a new value" rather than "use `nh_assign`").
- `@nh.tool` is discouraged. Binding functions are the preferred callable exposure mechanism. `design.md` documents `@nh.tool` as part of the specification. `tutorial.md` may mention it with a "prefer binding functions" note. All other files should not add examples, recommendations, or references to `@nh.tool`.
- The PyPI package name is `nighthawk-python`. Always use `nighthawk-python` (not `nighthawk`) in `pip install` commands and extras references (e.g., `nighthawk-python[claude-code-sdk]`).

### index.md specifics

- The documentation links list must stay in sync with the `nav` entries in `mkdocs.yml`.

### quickstart.md specifics

- Optimize for copy-paste. A new user should be able to run the first example within minutes.
- Keep troubleshooting entries to common first-run errors only.

### tutorial.md specifics

- Assumes the reader has completed quickstart.md. Do not re-explain setup beyond a brief reminder.
- Every section should teach one concept. Combine related ideas only when they share an example.
- `<!-- prompt-example:name -->` markers are test anchors verified by `tests/docs/test_prompt_examples.py`. Never modify the content between a marker pair without updating the corresponding test.
- Keep tutorial content backend-agnostic. Do not document backend-specific file layouts, provider credentials, or backend initialization variants here.
- If a concept needs backend-specific setup, add a short pointer to `providers.md` or `coding-agent-backends.md` instead of duplicating configuration details.

### design.md specifics

- This is the specification. Implementation should match this document; if they diverge, prefer changing the implementation (see Section 0.1 alignment policy).
- Use precise, unambiguous language. Avoid hedging ("usually", "typically") for specified behavior.
- Structure: numbered sections, decision notes, implementation notes. Keep the hierarchy stable; other docs link to specific section anchors.

### providers.md specifics

- The capability matrix must clearly show which features require a coding agent backend.
- Prefer concise, runnable setup snippets over conceptual narrative; link to `tutorial.md` for concept-first explanations.
- For custom backends, show the recommended path (`AgentStepExecutor.from_agent`) first, then the direct protocol implementation as an alternative.
- Credential details are not placed here. Pydantic AI provider credentials are delegated to the Pydantic AI documentation via external links.

### coding-agent-backends.md specifics

- Document shared capabilities (skills, MCP, working directory) once in a shared section, then keep per-backend sections focused on differences.
- For external CLI integrations, separate:
  - what Nighthawk configures and guarantees, and
  - what is delegated to backend CLI rules.
- Include a settings field table for each backend with type, default, and description columns.

### for-coding-agents.md specifics

- The reader is a coding agent (LLM), not a human. Write for immediate applicability, not progressive learning.
- Condense principles from tutorial.md, design.md, and writing guidelines into actionable rules. Do not duplicate prose; distill into decision rules and patterns.
- Include runnable code templates the agent can adapt directly.
- Keep the "common mistakes" table current; add entries when recurring issues are observed.
- This file should be self-contained: a coding agent reading only this file should be able to write correct Nighthawk code without consulting other docs.
- This file is consumed standalone (`@docs/for-coding-agents.md` in CLAUDE.md/AGENTS.md, GitHub raw URL, etc.). Do not assume sibling files exist at relative paths.
- All external references to other docs use absolute URLs based on `site_url` from `mkdocs.yml` (currently `https://kurusugawa-computer.github.io/nighthawk-python/`). If `site_url` changes, update the URLs in this file.
- `@nh.tool` must not appear in this file (see General rule on `@nh.tool`). Binding functions are the only callable exposure mechanism presented here.
- Filter content for coding-agent relevance. Omit infrastructure-level concerns (scoped overrides parameter lists, exception hierarchy beyond `ExecutionError`, observability/tracing) that do not affect how an agent writes Natural blocks or binding functions. Mention existence and link to Tutorial or Design for details.

### api.md specifics

- Content comes from source docstrings. Edit the source code, not api.md directly.
- Hand-editing is limited to `:::` directive structure (adding/removing sections, adjusting member filters).
- When the same module appears in multiple sections, use `members` filters in `:::` directives to partition members and avoid duplicate rendering.

### roadmap.md specifics

- Future-facing only. Remove items once they are implemented.
- Avoid implementation details; describe intent and motivation.
