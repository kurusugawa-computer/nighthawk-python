---
paths:
  - "docs/**/*.md"
---

# Documentation rules

## Canonical ownership

Each topic must have exactly one canonical owner file. Other files may restate the topic only as a deliberate derivative summary, quickstart, or routing pointer.

General rules:

- Prefer cross-references over duplication.
- If a topic appears in multiple files, one file must be the declared source of truth.
- Derivative documents may compress or subset canonical content when that serves a distinct audience.
- `for-coding-agents.md` is the main exception: it is a derivative operational guide for coding agents and may restate material from human-oriented docs.

Public API documentation layering:

- `api.md` owns the exhaustive inventory of the supported public API surface (existence, types, signatures, exceptions, docstrings). Low-level utilities and extension hooks belong here.
- `specification.md` owns API semantics, contracts, boundaries, and runtime behavior. It is the canonical source for *what a symbol means and how it behaves*.
- `quickstart.md`, `natural-blocks.md`, `patterns.md`, `runtime-configuration.md`, and `verification.md` are task-oriented. They cover *when to use* and *how to use* selected APIs -- not every public symbol. A public API having no coverage in these pages is expected when the symbol serves a narrow or advanced use case already documented by `api.md` and `specification.md`.

`docs/AGENTS.md` is governance-only content. It is a symlink to `.claude/rules/docs.md` and must not appear as an accidental published governance page. It is excluded from the published site via `exclude_docs` in `mkdocs.yml`.

## File roles and boundaries

| File | Audience | Role | Scope |
|---|---|---|---|
| `index.md` | First-time visitors | Landing page | Value proposition, one runnable example, entry path routing. No API, no how-to. |
| `quickstart.md` | New users | First success | Minimal setup, minimal example, minimal troubleshooting, explicit next-step link. |
| `natural-blocks.md` | Learners | What Natural blocks are | Natural block anatomy, prompt structure, read/write bindings, Pydantic model bindings, f-string injection, functions and discoverability, binding function design (principles and basic examples), responsibility split, structured output design. |
| `executors.md` | Learners / evaluators | Choose an execution backend | Capability / cost / latency matrix, decision tree, `StepExecutorConfiguration` basics, and routing to side references and `runtime-configuration.md`. |
| `runtime-configuration.md` | Learners | Configure execution | `nh.run()`, `nh.scope()`, configuration patching, prompt suffix fragments, context limits, JSON rendering style, and runtime execution identity. |
| `patterns.md` | Practitioners | Apply Natural blocks in workflows | Outcomes, deny frontmatter, error handling, custom exception types, async, carry pattern, cross-block composition, resilience patterns, and common mistakes. |
| `verification.md` | Practitioners | Verify and debug | Mock tests, integration tests, prompt inspection, diagnosing snipped markers, OpenTelemetry span hierarchy, step events, local trace inspection. |
| `pydantic-ai-providers.md` | Model configurers | Pydantic AI provider reference | Provider list, installation, model identifiers, credentials, model settings, provider-specific troubleshooting. No chooser. No custom backends. |
| `coding-agent-backends.md` | Backend users | Backend reference | Backend-specific settings, shared capabilities, skills, MCP tool exposure, working directory, troubleshooting. |
| `for-coding-agents.md` | Coding agents (LLMs) | Operational guide | Condensed, decision-oriented rules derived from human-oriented docs. Self-contained with absolute URLs. |
| `specification.md` | Implementors | Canonical spec | Syntax, state layers, tools, outcomes, frontmatter, runtime semantics, observability contract, and custom backend capability/protocol semantics. Numbered section headings. |
| `philosophy.md` | Evaluators | Design rationale | Execution model, design landscape, boundary-first consequences, trust and safety boundaries, tradeoffs, and runtime evaluation rationale. |
| `api.md` | Developers | API reference | Auto-generated from docstrings, including protocol and extension-hook symbols. |
| `roadmap.md` | Contributors | Future directions | Ideas only. Remove when implemented. |
| `docs/AGENTS.md` | Coding agents editing docs | Documentation governance | Canonical ownership, page roles, routing rules, and docs test invariants. Symlink to `.claude/rules/docs.md`. |

## Content routing

List only topics that commonly drift across multiple files or are easy to misplace.

- **Credentials** -> `quickstart.md` (minimal first run), `pydantic-ai-providers.md` (Pydantic AI providers), `coding-agent-backends.md` (backend prerequisites)
- **Executor selection** -> `executors.md` (capability and cost tradeoffs), `coding-agent-backends.md` (backend behavior and constraints), `for-coding-agents.md` (block-level operational guidance), `quickstart.md` (minimal entry example only)
- **Runtime setup and scoping** (`nh.run()`, `nh.scope()`, configuration patching) -> `runtime-configuration.md` (canonical), `patterns.md` (usage-only references), `for-coding-agents.md` (condensed)
- **Context limits / JSON rendering / execution identity** -> `runtime-configuration.md` (canonical), `verification.md` (`<snipped>` diagnosis only), `specification.md` (formal semantics), `for-coding-agents.md` (condensed)
- **Bindings** -> `natural-blocks.md` (canonical), `specification.md` (formal definition), `for-coding-agents.md` (condensed)
- **Binding function design** -> `natural-blocks.md` (principles and basic examples), `patterns.md` (only when binding functions participate in multi-block patterns), `for-coding-agents.md` (condensed)
- **Resilience** -> `patterns.md` (canonical patterns), `for-coding-agents.md` (condensed operational rules), `philosophy.md` (positioning only)
- **Testing** -> `verification.md` (canonical patterns and examples), `for-coding-agents.md` (condensed operational rules), `specification.md` (testing is out of scope except for boundary statements)
- **Observability** -> `verification.md` (usage and debugging workflow), `specification.md` (specification and runtime semantics), `for-coding-agents.md` (normally omit; mention only when needed to explain execution constraints)
- **Deny frontmatter** -> `patterns.md` (standard patterns), `specification.md` (canonical specification), `for-coding-agents.md` (condensed operational rules)
- **Coding agent control** -> `philosophy.md` (execution model and design consequences), `coding-agent-backends.md` (configuration and constraints)
- **Coding agent backend impact** -> `philosophy.md` (execution model and design consequences), `index.md` (brief summary), `coding-agent-backends.md` (details)
- **Async** -> `patterns.md` (patterns), `specification.md` (specification), `for-coding-agents.md` (condensed rules)
- **Structured output / Pydantic models** -> `natural-blocks.md` (design guidelines), `specification.md` (type validation specification)
- **Custom backends** -> `specification.md` (semantics), `api.md` (protocol symbols), `executors.md` (chooser-level mention only)
- **Workflow engine comparison** -> `philosophy.md` (canonical, design landscape section), `index.md` (link), `executors.md` (link)
- **Tool exposure tradeoffs** -> `philosophy.md` (canonical, design consequences section), `index.md` (link), `executors.md` (link)
- **Docs governance** -> `docs/AGENTS.md` and `.claude/rules/docs.md`. No derivative restatement elsewhere.

## Shared writing guidelines

### General

- Headings: sentence case. Capitalize first word, proper nouns (Nighthawk, Natural, Pydantic), acronyms (LLM, JSON, MCP).
- Anchors: name-based (`#writing-guidelines`), not number-based (`#1-writing-guidelines`). Exception: `specification.md` is a specification document and may use numbered section headings as its stable citation hierarchy.
- Cross-references: relative links. Exception: `for-coding-agents.md` uses absolute URLs from `site_url`.
- Terminology: "task" = structural unit (contract), "judgment" = cognitive act. Use "one task per block".
- Code examples: self-contained and understandable without surrounding prose.
- Built-in tools (`nh_eval`, `nh_assign`): implementation details. Only `specification.md` may expose them.
- `@nh.tool`: `specification.md` documents as spec, `natural-blocks.md` may mention it with a "prefer binding functions" note, all others must not reference it.
- Package name: always `nighthawk-python` in `pip install` commands.
- When renaming a document or changing its role, update all inbound references, routing rules here, relevant `tests/docs`, and navigation metadata if applicable.
- When a governance file under `docs/` is not meant for publication, its MkDocs handling must be explicit.
- `natural-blocks.md` and `patterns.md` together replace the former `guide.md`.
- References to `design.md` should use `specification.md`.
- References to `providers.md` should use `pydantic-ai-providers.md`.

### Prerequisite notes

Pages in the Getting started, Patterns & verification, and Configuration nav groups must open with a short prerequisite note. This supports non-linear readers who jump directly to a topic. The note should be one sentence naming the assumed prior reading. Pages in Reference, Background, and Project groups (`specification.md`, `philosophy.md`, `roadmap.md`, `api.md`) are exempt -- they serve independent audiences that do not follow the learning path.

## Per-file rules

**`index.md`**

- Links list must stay in sync with `nav` in `mkdocs.yml`.
- One representative code example (Python + Natural block + binding function). The example must be self-contained and runnable (include executor setup and `nh.run()` context).
- Brief positioning summaries linking to `philosophy.md`. No comparisons or benchmarks.

**`quickstart.md`**

- Optimize for copy-paste.
- Include only the minimum needed for a first success.
- Retain one explicit sentence stating the trust model / hard constraint for Natural blocks and imported markdown.
- End with a next-page link to `natural-blocks.md`.
- No backend alternatives beyond a one-line link to `executors.md`.

**`natural-blocks.md`**

- Prerequisite note: "This page assumes you have completed [Quickstart](quickstart.md)."
- Owns Natural block anatomy, prompt structure, binding semantics, discoverability, binding function design (principles and basic examples), responsibility split, and structured output design guidelines.
- Binding function design includes principles and basic examples that are complete within a single block. Advanced multi-block patterns (carry, branching, resilience) belong in `patterns.md`.
- Owns migrated `prompt-example` test anchors (`basic-binding`, `fstring-injection`, `local-function-signature`, `global-function-reference`). Exception: `carry-pattern` belongs in `patterns.md`.
- Backend-agnostic: examples use the Quickstart default executor.
- Cross-reference `specification.md` for formal definitions.
- Ends with a routing sentence: "Choosing an executor is in [Executors](executors.md). Runtime configuration (`nh.run()`, `nh.scope()`, limits) is in [Runtime configuration](runtime-configuration.md)."

**`executors.md`**

- Prerequisite note: "This page assumes you have completed [Quickstart](quickstart.md) and [Natural blocks](natural-blocks.md)."
- Owns executor selection: capability matrix, decision tree, and `StepExecutorConfiguration` basics.
- Links to `philosophy.md` for positioning instead of duplicating it.
- Capability matrix must include relative cost and latency columns.
- Must include an explicit custom-backend routing subsection with a minimal `AgentStepExecutor.from_agent(agent=agent)` runnable example (3-5 lines). Direct `AsyncStepExecutor` implementation belongs in `specification.md`.
- Ends with routing: side trips to `pydantic-ai-providers.md` and `coding-agent-backends.md`, then next-step link to `runtime-configuration.md`.
- Runtime configuration topics (`nh.run()`, `nh.scope()`, configuration patching, prompt suffix, context limits, JSON rendering, execution identity) belong in `runtime-configuration.md`, not here.

**`runtime-configuration.md`**

- Prerequisite note: "This page assumes you have completed [Executors](executors.md)."
- Owns all runtime configuration: `nh.run()`, `nh.scope()`, configuration patching, prompt suffix fragments, context limits, JSON rendering style, and runtime execution identity.
- These topics are independent of executor choice. The page applies equally to Pydantic AI providers and coding agent backends.
- Cross-reference `specification.md` for formal semantics.
- Ends with next-step link to `patterns.md`.

**`patterns.md`**

- Prerequisite note: "This page assumes you have completed [Natural blocks](natural-blocks.md) and [Runtime configuration](runtime-configuration.md)."
- Owns outcomes, deny, async, carry, composition, resilience, and common mistakes.
- Scope: multi-block coordination and operational patterns. Single-block-complete topics belong in `natural-blocks.md`.
- Backend-agnostic: no backend-specific file layouts or credentials.
- Cross-reference `specification.md` for formal definitions.

**`verification.md`**

- Prerequisite note: "Mock testing is readable after [Natural blocks](natural-blocks.md); later sections assume [Patterns](patterns.md)."
- Owns mock tests, integration tests, prompt inspection, debugging workflow, and OpenTelemetry usage.
- Normative observability contracts belong in `specification.md`.

**`pydantic-ai-providers.md`**

- Prerequisite note: "See [Executors](executors.md) for choosing between providers, backends, and custom executors."
- Pure Pydantic AI provider reference: installation, model identifiers, model settings, troubleshooting.
- No chooser table.
- No custom backends.

**`coding-agent-backends.md`**

- Prerequisite note: "See [Executors](executors.md) for when to choose a coding agent backend over a provider-backed executor."
- Reference-first page: minimal orientation only, then backend-specific settings, skills, MCP, working directory, and troubleshooting.
- Must not become a second chooser page. Capability, latency, cost, and positioning comparisons belong in `executors.md` and `philosophy.md`.
- Shared capabilities section for common features.
- Reference `executors.md` for capability and cost comparisons.

**`specification.md`**

- All current specification rules apply, with the new name.
- Numbered section headings remain stable.
- Owns custom backend capability/protocol semantics, placed as a subsection under Section 14 (Step executor) to avoid top-level section number disruption.
- Includes a non-runnable skeletal shape of the `AsyncStepExecutor` protocol surface under Section 14. No runnable implementation example is required (the runnable `from_agent` example lives in `executors.md`).

**`philosophy.md`**

- Owns the cumulative argument: execution model, design landscape, boundary-first consequences (resilience, scoped execution contexts, tool exposure, multi-agent coordination), trust and safety boundaries, tradeoffs, and runtime evaluation rationale.
- External references acceptable. Prefer stable URLs with enough inline context to survive link rot.
- No how-to code examples for patterns in `natural-blocks.md` or `patterns.md`. Exception: positioning examples may reuse function names from those pages.

**`for-coding-agents.md`**

- Reader is a coding agent. Write for immediate applicability with runnable templates and decision rules.
- Information flows from human-oriented docs only; never introduce new product behavior here first.
- May derive from `natural-blocks.md`, `patterns.md`, `runtime-configuration.md`, `verification.md`, `specification.md`, `pydantic-ai-providers.md`, and `coding-agent-backends.md`.
- Self-contained with absolute URLs from `site_url` (`https://kurusugawa-computer.github.io/nighthawk-python/`).
- Prefer decision rules over encyclopedic coverage.
- Recommend provider-backed executors by default and coding agent backends only for blocks that need autonomous long-horizon work.
- Keep trust-model constraints explicit.
- Condensation policy: compress tables and lists to inline summaries or subsets with links to canonical docs. Verbatim duplication only for compact, self-contained content.
- Common mistakes: subset of most impactful items with link to fuller guidance.
- Include resilience and scoped overrides.
- Omit observability except when needed to explain execution constraints.
- Omit exception hierarchy beyond `ExecutionError` unless a narrower rule is essential for safe coding.
- Published as a derivative operational reference under `Reference` in nav, not as a top-level learner-path peer.
- Absolute URLs use topic-based canonical owner mapping:
  - Bindings, block anatomy, responsibility split, binding function design -> `/natural-blocks/`
  - Carry, deny, async, resilience, multi-block composition -> `/patterns/`
  - Executor selection, `StepExecutorConfiguration` basics -> `/executors/`
  - `nh.run()`, `nh.scope()`, configuration patching, context limits, JSON rendering, execution identity -> `/runtime-configuration/`
  - Provider-specific setup -> `/pydantic-ai-providers/`
  - Coding agent backend config -> `/coding-agent-backends/`
  - Spec references -> `/specification/`

**`api.md`**

- Exhaustive inventory of the supported public API surface. Every supported public symbol should appear here.
- Auto-generated from source docstrings. Hand-editing limited to `:::` directive structure.
- Use `members` filters to avoid duplicate rendering.
- A symbol appearing only in `api.md` (with no learner-facing page coverage) is acceptable. Task-oriented docs select symbols by pedagogical value, not completeness.

**`roadmap.md`**

- Future-facing only. Remove items once implemented.
- Each item should reference the relevant `specification.md` section where that helps maintain traceability.

**`docs/AGENTS.md`**

- Is a symlink to `.claude/rules/docs.md`. No separate synchronization step needed.
- Must not appear as an accidental published governance page. Exclude via `exclude_docs` in `mkdocs.yml`, listing `AGENTS.md` explicitly by filename.

## Documentation test invariants

- Treat executable or doctrinal claims in docs as testable when practical.
- `prompt-example` anchors live in `natural-blocks.md` by default (`basic-binding`, `fstring-injection`, `local-function-signature`, `global-function-reference`). Exception: `carry-pattern` lives in `patterns.md` (cross-block composition). Test file: `tests/docs/test_prompt_examples.py`.
- `for-coding-agents.md` operational examples and core doctrine are guarded by `tests/docs/test_coding_agent_examples.py`. Update the tests when changing executable guidance or non-negotiable rules.
- When a docs change invalidates an existing test, first decide whether the docs or the test is the canonical truth for that claim, then update both sides to match.
- Docs architecture regression tests (`tests/docs/test_docs_architecture.py`):
  - Fail on stale references to deleted/renamed docs (`guide.md`, `design.md`, `providers.md`).
  - Guard the canonical example relationship between `index.md` and `README.md` via fenced-code-block extraction + normalized exact match.
  - Guard selected canonical-owner expectations where drift is likely.
  - Automate `mkdocs.yml` nav entries vs `docs/` file existence as a pytest case.
  - Guard that obsolete canonical pages do not remain published accidentally alongside their replacements.
- `mkdocs build` must succeed without `--strict`. Warnings are acceptable.
- All internal relative links must resolve.
