---
paths:
  - "docs/**/*.md"
---

# Documentation

## Ownership

This file owns documentation responsibilities and editorial constraints.
`CONTRIBUTING.md` owns development setup, change and review procedures, verification commands, and experiment authorization.
Keep one canonical owner for each subject.
Other pages may summarize it for their audience and link to the owner; they must not create independent guarantees.
`api.md` owns the exhaustive public symbol inventory and rendered signatures/docstrings.
`specification.md` owns behavior, contracts, and boundaries.
Task-oriented pages teach selected APIs rather than duplicating either reference.

| Page | Audience and responsibility |
| --- | --- |
| `index.md` | First-time visitors: value proposition, one runnable example, when to use Nighthawk, and navigation. |
| `quickstart.md` | New users: installation, credentials, first successful execution, minimal troubleshooting. |
| `natural-blocks.md` | Learners: block anatomy, bindings, interpolation, discovery, helper design, and structured outputs. |
| `decisions.md` | Learners: typed judgments, explicit evidence, decision models, and per-step results. |
| `executors.md` | Evaluators: backend selection, capability and resource tradeoffs, and entry points for custom execution. |
| `runtime-configuration.md` | Application authors: runs, scopes, models, context limits, rendering, prompt suffixes, and execution identity. |
| `patterns.md` | Practitioners: control outcomes, deny frontmatter, errors, async composition, carry, and resilience. |
| `verification.md` | Practitioners: deterministic tests, provider tests, prompt inspection, traces, and lifecycle evidence. |
| `host-integration.md` | Hosts: function admission, model policy, request accounting, and interruption cleanup. |
| `operation-feedback.md` | Hosts: operation refusal, correction, and terminal abortion. |
| `embedded-execution.md` | Hosts: explicit definitions, namespaces, validation, and writeback. |
| `pydantic-ai-providers.md` | Provider users: dependencies, identifiers, credentials, settings, and troubleshooting. |
| `coding-agent-backends.md` | Coding-agent users: prerequisites, settings, skills, tool transport, working directories, and limitations. |
| `for-coding-agents.md` | Agent authors: condensed operational guidance derived from the task-oriented pages and specification. |
| `philosophy.md` | People and AI agents designing Nighthawk and programs built with it: explain what Nighthawk aims to make possible, why its foundational design choices serve that purpose, and how to evaluate tradeoffs and proposed changes. |
| `design-drivers.md` | Designers: application needs, inclusion policy, evaluation evidence, and implications for Core. |
| `roadmap.md` | Contributors: exploratory directions; remove completed items. |

Route provider credentials to the corresponding provider/backend page, with only first-run setup in Quickstart.
Keep backend comparison in `executors.md`; provider and backend references describe their selected implementation.
Keep configuration in `runtime-configuration.md`; patterns use it and link back.
Keep single-block helper design in `natural-blocks.md` and multi-block composition in `patterns.md`.
Separate application evidence from runtime guarantees using `design-drivers.md`.

## Writing and navigation

- Use sentence-case headings, relative cross-references, and semantic line breaks.
- Use topic-based anchors; the specification retains its numbered citation hierarchy.
- State current behavior directly. Remove obsolete API migrations and document-rename history from guides and governance.
- Use "task" for the structural unit and "judgment" for the cognitive act; teach one task per block.
- Keep code examples self-contained. Use `nighthawk-python` as the installation package name.
- Built-in tools such as `nh_evaluate` and `nh_assign` belong to the specification and explicit host/backend adapter examples. Learner guidance uses Python helpers and bindings.
- Native tools configured through `nh.scope(tools=...)` belong in the specification and runtime configuration guide. Natural blocks may mention them while preferring binding functions.
- Pages in Getting started, Patterns & verification, Configuration, and Host integration open with a short prerequisite note naming relevant prior reading.
- When a page's name or responsibility changes, update inbound links, navigation, ownership rules, and relevant documentation tests together.
- `docs/AGENTS.md` is a symlink to this governance file. Keep it explicitly excluded from publication in `mkdocs.yml`.

## Page-specific constraints

- `index.md` and README share the same value proposition, runnable Python/Natural example, and guidance on when to use Nighthawk. Keep landing-page links consistent with navigation and link to the philosophy for design rationale.
- `quickstart.md` optimizes for copy-paste, states that Natural sources and included Markdown must be trusted, and ends with a link to Natural blocks. Route backend alternatives through Executors.
- `natural-blocks.md` uses the Quickstart executor, links to formal binding contracts, and routes onward to Executors and Runtime configuration.
- `executors.md` distinguishes capability, cost, and latency tradeoffs and includes a small `AgentStepExecutor.from_agent(agent=agent)` example. Direct executor protocol semantics belong in the specification.
- `patterns.md` stays backend-agnostic and keeps provider setup in the provider/backend references.
- `verification.md` explains observability usage; formal delivery guarantees belong in the specification.
- `philosophy.md` connects Nighthawk's purpose to its foundational design choices, their rationale and costs, and principles for evaluating tradeoffs and proposed changes. Keep those connections useful when APIs change, and distinguish Nighthawk's design commitments from application-owned goals and acceptable risks. Route behavior and guarantees to the specification, application workflows and evidence to Design drivers, usage to guides, development approval procedures to `CONTRIBUTING.md`, and guidance on when to use Nighthawk to `index.md` and README.
  Give each principle one design decision, with a concise rule, rationale, and relevant conditions. Invariants hold in every design; each default states its preference and when another choice is better. Use enough explanation to make the decision clear without repeating other principles or their operational details.
  Add a principle only when it remains useful across implementations and applications, informs a distinct design decision, and is not adequately covered by an existing principle.
  Before adding content, consider integrating it with existing text, replacing weaker wording, or placing detail with its authoritative owner. Retain the explanation needed to understand the rationale and conditions.
  Assess growth by the design decisions it improves. Word counts may help notice growth, but impose no fixed word limit, equal-length replacement rule, or automated word-count acceptance check.
- `roadmap.md` contains ideas, with links to relevant existing contracts when useful; it does not promise behavior.
- `api.md` uses explicit member filters to avoid duplicate rendering. Edit directive structure, keeping API prose in source docstrings.

### Coding-agent guide

`for-coding-agents.md` is a self-contained derivative operational reference.
It may condense canonical content but must not introduce product behavior first.
Use absolute URLs under the configured `site_url` so excerpts remain usable outside the site.
Prefer actionable rules and runnable templates, covering trust, explicit bindings, typed judgment, scoped configuration, composition, and recovery.
Recommend provider-backed execution by default and coding-agent backends when the task needs autonomous extended work.
Link detailed inventories, observability, and exception semantics to their owners.

## Verification

Test current executable claims, public symbols, and rendered links.
Do not assert exact prose or retain tests solely to remember removed names and paths.

| Test owner | Contract |
| --- | --- |
| `tests/docs/test_integrity.py` | Shared README/index example and explicit API members. |
| `tests/docs/test_links.py` | Strict site build, navigation targets, and rendered internal links and anchors, including absolute documentation URLs. |
| `tests/docs/test_prompt_examples.py` | Marked prompt examples in Natural blocks and the carry example in Patterns. |
| `tests/docs/test_coding_agent_examples.py` | Runnable coding-agent guidance. |
| `tests/docs/test_runtime_examples.py` | Marked runtime, host, embedded, and verification examples, also against an installed wheel. |

Keep `prompt-example` anchors with their examples: `basic-binding`, `fstring-injection`, `local-function-signature`, and `global-function-reference` in Natural blocks; `carry-pattern` in Patterns.
When an example changes, update its behavior checks as well.
Follow the documentation verification commands in `CONTRIBUTING.md`.
