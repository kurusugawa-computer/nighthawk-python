---
paths:
  - "docs/**/*.md"
---

# Documentation rules

## File roles and boundaries

Content belongs in exactly one file; cross-reference rather than duplicate. Exception: `for-coding-agents.md` condenses (distills) content from other files into actionable rules.

| File | Audience | Role | Scope boundary |
|---|---|---|---|
| `index.md` | First-time visitors | Project overview, motivation, workflow styles | What Nighthawk is and why. Brief positioning summary with link to `philosophy.md`. No API details, no how-to. |
| `philosophy.md` | Users evaluating Nighthawk | Deep positioning and design rationale | Workflow styles, comparison with workflow engines, tool exposure tradeoffs (MCP/CLI/binding functions), runtime evaluation rationale. Technical arguments with benchmarks. |
| `quickstart.md` | New users | Shortest path to running a Natural block | Setup, first example, credentials, troubleshooting. No deep explanations. |
| `tutorial.md` | Users learning the system | Build understanding from first principles | Bindings, functions and discoverability, control flow, composition, configuration, async. Assumes quickstart is done. Guidelines and testing are in `practices.md`. |
| `practices.md` | Users applying patterns | Practical patterns and guidelines | Writing guidelines, binding function design, testing and debugging, observability. Assumes tutorial is done. |
| `design.md` | Implementors and advanced users | Canonical specification (target behavior) | Full technical detail: syntax rules, state layers, prompt rendering, tool contracts, outcome schema, frontmatter. |
| `providers.md` | Users choosing and configuring models | Provider selection, Pydantic AI setup, custom backends | Provider categories, capability matrix, model identifiers, Pydantic AI model settings, step executor protocols. No coding-agent-backend-specific content. |
| `coding-agent-backends.md` | Users of Claude Code or Codex backends | Coding agent backend configuration and features | Backend-specific settings, skills, MCP tool exposure, working directory, project-scoped files. |
| `for-coding-agents.md` | Coding agents (LLMs) working on Nighthawk projects | Condensed development knowledge base | Nighthawk mental model, Natural block writing, binding function design, control flow, composition, testing, common mistakes. Not a human tutorial; an LLM reference. |
| `api.md` | Developers using the library | Auto-generated API reference (mkdocstrings) | Public API surface only. Content comes from source docstrings; do not hand-edit. |
| `roadmap.md` | Contributors and planners | Future directions | Ideas and desired directions only. Must not restate what is already implemented. |

## Content routing (non-obvious cases)

Most content maps to exactly one file via the scope boundaries above. These cases involve splits or non-intuitive placement:

- **Credential setup** -> `quickstart.md` (`OPENAI_API_KEY` only), `providers.md` (Pydantic AI providers), `coding-agent-backends.md` (coding agent backends)
- **Error types** -> `design.md` (specification), `tutorial.md` (practical usage with examples)
- **Testing patterns** -> `practices.md` (patterns with examples), `for-coding-agents.md` (condensed rules)
- **Writing guidelines** -> `practices.md` (patterns with examples), `for-coding-agents.md` (condensed rules)
- **Observability** -> `practices.md` (practical setup), `design.md` (specification)
- **Conceptual impact of coding agent backends** (how they expand what Natural blocks can do) -> `philosophy.md` (positioning arguments), `index.md` (brief summary), `practices.md` (guidelines). Configuration details stay in `coding-agent-backends.md`.

## Writing guidelines

### General

- Cross-reference with relative links (e.g., `[Section 5](tutorial.md#5-cross-block-composition)`). Exception: `for-coding-agents.md` uses absolute URLs based on `site_url` from `mkdocs.yml`.
- File boundary delineation: `index.md` owns "why" (motivation, positioning); `tutorial.md` owns "how" (usage with examples); `practices.md` owns "how to do it well" (guidelines, patterns, testing); `design.md` owns "exact rules and edge cases" (specification).
- Maintain consistent terminology across files (e.g., "one task per block" everywhere, not "one judgment" in one file and "one task" in another).
- Keep code examples self-contained: understandable without surrounding prose.
- Built-in tool names (`nh_eval`, `nh_exec`, `nh_assign`) are implementation details. Only `design.md` may expose them; all other files describe behavior instead.
- `@nh.tool` is discouraged. `design.md` documents it as specification. `tutorial.md` may mention it with a "prefer binding functions" note. All other files (including `for-coding-agents.md`) must not reference it.
- The PyPI package name is `nighthawk-python`. Always use `nighthawk-python` in `pip install` commands and extras.
- Terminology: "task" refers to the structural unit a Natural block performs (contract: inputs, outputs, outcome). "judgment" refers to the cognitive act the LLM performs (classification, interpretation, generation). Use "one task per block", not "one judgment per block".

### Per-file rules

**index.md**
- Documentation links list must stay in sync with `nav` entries in `mkdocs.yml`.
- Keep positioning sections as brief summary paragraphs linking to `philosophy.md`. Do not add detailed comparisons, benchmarks, or technical arguments here.

**philosophy.md**
- Owns all detailed positioning arguments: workflow styles, workflow engine comparison, tool exposure tradeoffs, runtime evaluation rationale.
- External references (benchmarks, blog posts) are acceptable. Prefer stable URLs; include enough inline context that the argument survives link rot.
- May reference `tutorial.md` and `coding-agent-backends.md` for cross-cutting concepts but must not duplicate how-to content.

**quickstart.md**
- Optimize for copy-paste. Keep troubleshooting to common first-run errors only.
- Includes both a Pydantic AI provider example and a coding agent backend (claude-code-cli) example.

**tutorial.md**
- One concept per section. Combine related ideas only when they share an example.
- `<!-- prompt-example:name -->` markers are test anchors verified by `tests/docs/test_prompt_examples.py`. Never modify content between markers without updating the test.
- Backend-agnostic in configuration: no backend-specific file layouts, credentials, or initialization variants; point to `providers.md` or `coding-agent-backends.md` instead. Conceptual references to what coding agent backends enable are acceptable as brief pointers.

**practices.md**
- Assumes the reader has completed the tutorial. No repetition of fundamentals.
- No `<!-- prompt-example:name -->` markers; all prompt examples remain in `tutorial.md`.
- Focus on practical application patterns, setup instructions, and decision frameworks.
- Cross-reference `tutorial.md` for concepts, `design.md` for specifications.

**design.md**
- The specification. If implementation diverges, prefer changing the implementation (Section 0.1).
- Precise, unambiguous language. No hedging for specified behavior.
- Keep section hierarchy stable; other docs link to anchors.

**providers.md**
- Capability matrix must clearly show which features require a coding agent backend.
- Concise, runnable setup snippets over narrative; link to `tutorial.md` for concepts.
- Custom backends: show `AgentStepExecutor.from_agent` snippet; link to `design.md` for protocol details.
- No credential details; delegate to Pydantic AI documentation.

**coding-agent-backends.md**
- Document shared capabilities once in a shared section; per-backend sections focus on differences.
- For CLI integrations, separate what Nighthawk configures from what is delegated to backend CLI rules.
- Include a settings field table per backend (type, default, description).
- Reference `providers.md` for the overall provider landscape and capability matrix; do not duplicate the matrix.

**for-coding-agents.md**
- The reader is a coding agent (LLM). Write for immediate applicability, not progressive learning. Include runnable code templates.
- Distill principles from tutorial.md, practices.md, design.md, and guidelines into actionable rules. Do not duplicate prose.
- Information flows from human-oriented docs to this file, never the reverse. All facts, patterns, and rules in this file must have a source in tutorial.md, practices.md, or design.md. Do not introduce new information here.
- Self-contained: readable without sibling files. All doc references use absolute URLs from `site_url` in `mkdocs.yml` (currently `https://kurusugawa-computer.github.io/nighthawk-python/`). Update URLs if `site_url` changes.
- Keep the "common mistakes" table current.
- Filter for coding-agent relevance: omit infrastructure concerns (scoped overrides, exception hierarchy beyond `ExecutionError`, observability) that don't affect writing Natural blocks or binding functions.

**api.md**
- Content from source docstrings; edit source code, not api.md. Hand-editing limited to `:::` directive structure.
- Use `members` filters in `:::` directives to avoid duplicate rendering when the same module appears in multiple sections.

**roadmap.md**
- Future-facing only. Remove items once implemented. No implementation details.
