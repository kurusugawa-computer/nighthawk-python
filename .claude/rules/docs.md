---
paths:
  - "docs/**/*.md"
---

# Documentation rules

## File roles and boundaries

Each file has a distinct audience and scope. Content belongs in exactly one file; cross-reference rather than duplicate.

| File | Audience | Role | Scope boundary |
|---|---|---|---|
| `index.md` | First-time visitors | Project overview, motivation, workflow styles | What Nighthawk is and why. No API details, no how-to. |
| `quickstart.md` | New users | Shortest path to running a Natural block | Setup, first example, backends table, credentials, troubleshooting. No deep explanations. |
| `tutorial.md` | Users learning the system | Build understanding from first principles | Bindings, tools, control flow, composition, configuration, guidelines. Assumes quickstart is done. |
| `design.md` | Implementors and advanced users | Canonical specification (target behavior) | Full technical detail: syntax rules, state layers, prompt rendering, tool contracts, outcome schema, frontmatter. |
| `providers.md` | Users configuring models | LLM provider setup and backend-specific settings | Provider categories, model identifiers, backend settings, credential conventions. |
| `api.md` | Developers using the library | Auto-generated API reference (mkdocstrings) | Public API surface only. Content comes from source docstrings; do not hand-edit. |
| `roadmap.md` | Contributors and planners | Future directions | Ideas and desired directions only. Must not restate what is already implemented. |

## What goes where (decision guide)

- **Syntax rule or runtime contract?** -> `design.md`
- **How to use a feature with examples?** -> `tutorial.md`
- **Model/provider configuration?** -> `providers.md`
- **First-time setup or "just make it work"?** -> `quickstart.md`
- **Not yet implemented?** -> `roadmap.md`
- **Public API signature or docstring?** -> `api.md` (edit the source docstring, not api.md)

## Writing guidelines for docs/

### General

- Cross-reference with relative links (e.g., `[Section 5](tutorial.md#5-cross-block-composition)`) instead of duplicating content.
- When tutorial.md and design.md cover the same concept, tutorial.md shows the "what and how" with examples; design.md specifies the "exact rules and edge cases".
- Keep code examples self-contained: a reader should understand the example without reading surrounding prose.

### tutorial.md specifics

- Assumes the reader has completed quickstart.md. Do not re-explain setup beyond a brief reminder.
- Every section should teach one concept. Combine related ideas only when they share an example.
- `<!-- prompt-example:name -->` markers are test anchors verified by `tests/docs/test_prompt_examples.py`. Never modify the content between a marker pair without updating the corresponding test.
- Avoid exposing built-in tool names (`nh_eval`, `nh_exec`, `nh_assign`) in tutorial text. These are implementation details covered by design.md. Describe behavior instead (e.g., "the LLM can mutate the object in-place").

### design.md specifics

- This is the specification. Implementation should match this document; if they diverge, prefer changing the implementation (see Section 0.1 alignment policy).
- Use precise, unambiguous language. Avoid hedging ("usually", "typically") for specified behavior.
- Structure: numbered sections, decision notes, implementation notes. Keep the hierarchy stable; other docs link to specific section anchors.

### quickstart.md specifics

- Optimize for copy-paste. A new user should be able to run the first example within minutes.
- Keep troubleshooting entries to common first-run errors only.

### roadmap.md specifics

- Future-facing only. Remove items once they are implemented.
- Avoid implementation details; describe intent and motivation.
