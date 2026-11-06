# Preserving Python assessment work after Natural generation

Investigation date: 2026-10-21.
Baseline revision: `450c492`.
The corrected fixture and guidance are committed with this record.

## Application and acceptance criteria

`tests/integration/test_natural_composition.py` contains a synthetic reporting pipeline.
A Natural block generates exactly two summary lines from team ticket counts.
Python then calls a Natural assessor once per line and returns a list of `(line, confidence)` pairs.
Acceptance requires the two supported claims, one completed assessment for each claim, correct lifecycle relationships, and confidence in the expected range.
A summary string alone does not complete the application task.

The same file checks model-selected child assessments with an explicitly shared review policy, and ten actual child calls with explicit iteration arguments.
These are runtime composition and model-behavior checks, not a comparison with ordinary Python or an evaluation of agent authorship.

## Observed failure

The first run used the baseline pipeline with the workspace's binding-parser fixes.
For `gpt-5.6-luna`, the summarization block selected a function-level `return` containing:

```text
Willow team completed 12 tickets.
Birch team completed 18 tickets.
```

The block had not forbidden `return`.
Core rejected the string against the outer function's `list[tuple[str, float]]` return contract and reported `Return value validation failed`.
The Python assessment loop did not run.
The output binding's string contract could not establish completion of the larger pipeline.

The wider integration run was interrupted deliberately to inspect this failure after 199.30 seconds.
Pytest reported one failed test, seven passed tests, and twelve skipped coding-agent tests.
This interrupted run is not an all-pass result; its completed successes do not erase the failure.

## Change and verification

The generating block now declares `deny: [return]`, and the guide presents the same contract.
Its `summary: str` annotation makes the intermediate value contract explicit.
Normal completion proceeds to Python's assessment loop; runtime and helper failures can still interrupt execution.
No new Core control-flow primitive or automatic recovery was introduced.

The complete composition suite then passed all six cases in 231.11 seconds:

| Scenario | gpt-5.6-luna | gpt-5.6-sol |
| --- | --- | --- |
| Python iteration assesses each generated summary line | Pass | Pass |
| Model-selected children receive the explicit review policy | Pass | Pass |
| Parent materializes its counter before ten actual child calls | Pass | Pass |

Reproduction from the repository root, with authorized credentials in `.env`:

```sh
NIGHTHAWK_OPENAI_INTEGRATION_TESTS=1 uv run --env-file .env pytest -q -x --tb=short tests/integration/test_natural_composition.py
```

Environment: CPython 3.13.12, Pydantic 2.14.1, Pydantic AI 2.58.0, OpenAI 3.35.1, pytest 9.1.1.
Both models used the OpenAI Responses provider, medium reasoning, `max_tokens=8192`, and a 90-second request timeout.
Fixture-level timeouts allow 360 seconds for the first two scenarios and 600 seconds for the ten-child scenario.

There was one post-change run per model/scenario.
These observations establish that the checked compositions succeeded in those runs; they do not estimate a reliable failure rate, prove semantic correctness for other inputs, or establish a comparative advantage.
The deterministic runtime contract, rather than successful model cooperation, enforces the denied outcome.

## Workspace validation accompanying the change

- The final deterministic suite passed 1,292 tests and skipped 35 opt-in or interpreter-specific checks in 48.20 seconds.
  One upstream `pydantic_graph` event-loop deprecation warning remained.
- The separate OpenAI execution, decision, and multimodal suites passed nine cases in 40.81 seconds.
- After the final interpolation change, the carry suite passed all three cases, including f-string injection, in 35.27 seconds.
  Together with the six composition cases, the completed OpenAI suites cover all eighteen enabled integration cases.
- Ruff lint and formatting checks, Pyright, and the strict documentation build passed.
- A wheel built from the source distribution was installed outside the repository with only its declared runtime dependencies.
  Ten published examples and one strict consumer type check passed in 6.05 seconds.
  The consumer check uses the installed environment and disables inference from untyped library source.
- Temporarily removing `py.typed` from that disposable installed package made the consumer test fail with missing-type-stub and unknown-decorator diagnostics.
  The marker was restored immediately.
  This checks selected exported types and synchronous/asynchronous decorator signatures; it is not a claim of complete public-API type coverage.

Coding-agent services and other Python interpreters were not exercised in this investigation.
No publication or deployment was performed.
