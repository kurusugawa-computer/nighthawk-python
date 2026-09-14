# Coding-agent guide assessment

## Purpose and scope

Assess whether `docs/for-coding-agents.md` supports basic Nighthawk application design, implementation, and contract testing without supplementary Nighthawk material.
Advanced features may require the task-specific documentation links.
This implements the ratified P-CORE-001, P-EXAMPLE-001, P-LINK-001, and P-REVIEW-001 decisions.

The guide now explains judgment boundaries, typed intermediates, Python guarantees, uncertainty, state, effects, and verification.
It includes a classification template, an order interpretation and pricing example, and executable contract tests.
Advanced runtime details are organized by the task that requires reading their full contracts.

## Transfer evaluation

A fresh coding-agent session received the guide and the following application requirements without the parent conversation or other Nighthawk documentation:

1. Assess every customer complaint in input order, interpret an urgency of `urgent` or `routine`, and compute a deadline of 4 or 48 hours using trusted host policy.
   Do not send messages.
   Include deterministic tests for coverage, host policy, malformed output, and outcomes that could bypass checks.
2. Design a repository investigation task that may choose which files or helpers to inspect, returns a typed finding, and requires host approval before applying any patch.
   Identify linked contracts needed before implementing advanced features.

The evaluator could execute its own Python files against the installed library, but could not read other repository documentation, source, tests, or links, or make provider calls.

The implementation used a Natural urgency judgment, a Python loop retaining per-item failures, and a Python mapping from urgency to deadlines.
It passed 10 deterministic tests covering order, duplicate complaints, host-selected deadlines, malformed results, unassigned outputs, and empty input.
The evaluator identified that the guide did not explain how to construct prohibited return and raise outcomes in tests.

The guide was updated with `return_response`, `raise_response`, and the expected rejection behavior.
Using only that addition, the evaluator added four cases covering forbidden outcomes both at the judgment boundary and in the batch.
All 14 tests passed.
Rejected outcomes did not produce deadlines, and later complaints were still processed in order.

The investigation design put evidence selection in Natural, typed finding validation in Python, and approval and patch application in a separate host operation.
It recognized that output validation does not enforce read-only access or roll back effects, and identified the backend, execution-boundary, and configuration documentation as prerequisites for implementation.

The evaluator's application and tests are session artifacts under `/tmp/nighthawk-guide-transfer`.
The command used was:

```sh
cd /tmp/nighthawk-guide-transfer
/workspaces/nighthawk-python/.venv/bin/python -m pytest -q test_complaints.py
```

This is one qualitative transfer exercise, not a model comparison or evidence of broad agent reliability.
The follow-up is a targeted check of the identified omission, not a second independent trial.
The complaint prompt's definition of urgency was an evaluator assumption because the application requirement supplied labels but no domain-specific urgency policy.
Real-model complaint semantics and the investigation backend were not tested.

## Published example verification

`tests/docs/test_coding_agent_examples.py` extracts the Python blocks directly from the guide and executes the template and published contract tests.
It mocks only provider results, retaining Natural source transformation and runtime output validation.
This checks the actual text readers copy without maintaining a duplicate implementation.

Validation commands:

```sh
uv run --no-sync pytest -q tests/docs
uv run --no-sync ruff check tests/docs/test_coding_agent_examples.py
uv run --no-sync ruff format --check tests/docs/test_coding_agent_examples.py
uv run --no-sync pyright tests/docs/test_coding_agent_examples.py
uv run --no-sync mkdocs build --strict --site-dir /tmp/nighthawk-guide-review/site
```

The documentation suite passed all 14 tests.
The guide's three published quote tests also passed when extracted as `quotes.py` and `test_quotes.py` and executed directly with pytest.
Lint, formatting, type checking, and the strict documentation build passed.
All 18 published documentation links were checked against the corresponding local pages and heading anchors.

## Real-model example checks

The classification and quote examples were extracted unchanged from the guide into `/tmp/nighthawk-guide-review`.
Tests used `openai-responses:gpt-5.6-luna` through the documented `AgentStepExecutor` configuration and the authorized `.env` credential.
No credentials were included in evaluation artifacts.

The quote checks repeat each case twice, using a host price of 250 cents:

| Input | Expected behavior |
| --- | --- |
| `A dozen, please.` | 3000 cents |
| `Please send three.` | 750 cents |
| `Two pairs, please.` | 1000 cents |
| `Please send one hundred items.` | 25000 cents |
| `A few, please.` | Request clarification |
| `Either three or five, I have not decided.` | Request clarification |
| `Please send 101 items.` | Request clarification |
| `Please send zero items.` | Request clarification |

Classification checks use the documented crash report (`bug`), a request for an export-to-PDF button (`feature`), and a question about account settings (`question`).
Assertions check the expected semantics and Python totals or clarification exception, not just output type or range.

All 19 real-model cases passed in 180.68 seconds: 16 quote cases and three classification cases.
An initial run without enabled egress produced no completed test results and was interrupted; the completed run used authorized egress.
The real-model tests are session artifacts in `/tmp/nighthawk-guide-review/test_quotes_live.py`.

These are small synthetic examples with limited repetitions.
They do not establish reliability on production inputs, security against arbitrary input, or performance across models and backends.

Guide SHA-256 at evaluation completion: `0bb5994a8ef01cc358da7ec7a96d5602bb7daf4f8cb2decfd6c17a279e12170c`.
