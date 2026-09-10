# Actual Natural composition integration evidence

## Scope and execution

The [Ideal State examples](../../../docs/ideal-state.md#9-behavioral-examples-and-acceptance-criteria) require actual model-backed composition in addition to deterministic environment tests.
The new [integration module](../../../tests/integration/test_natural_composition.py) exercises three workflows using production source instrumentation, context construction, operations, finalization, and lifecycle delivery.
Every child in these tests runs another Natural block against the model; no scripted child substitutes for it.

On 2026-09-10, the three cases passed using `openai-responses:gpt-5.6-sol`, medium reasoning effort, an 8,192-token response limit, and a 90-second model-request timeout.
The Python-iteration and policy-review workflows have 360-second deadlines; the ten-child workflow has a 600-second deadline.
The run took 153.81 seconds, including test setup and reporting.
It used the production implementation at `149f16f`, plus the integration module introduced with this evidence.

| Workflow | Natural steps | Observed result | Test duration |
| --- | ---: | --- | ---: |
| Python summarizes, then iterates over the summary | 3 | Two nonempty claims retain the named teams and ticket counts; each actual child receives its corresponding line and returns a confidence of at least 0.8. | 14.151 s |
| Natural selects follow-up assessments | 3 | Only the numerical and causal claims reach children, each once; the documented numerical claim scores at least 0.8 and the unsupported causal assertion scores at most 0.2. | 24.050 s |
| Natural materializes a counter and calls ten children | 11 | Children receive parent counter values 1 through 10; the fifth and eighth return their designated labels and all others return `ordinary`. | 115.349 s |

The runtime supplied one start and one successful terminal event for each of the 17 steps, with unique invocation identities.
Each model-invoked child started and finished within its parent's active lifetime, and its terminal event identified that parent.
The summary assessor calls originate in ordinary Python after the summarization step has finished, so their parent step identities are empty.
The parent receives the actual completed child values, including when a child chooses a permitted direct `Return`.

## What the oracles verify

The summary fixture contains two fixed facts about fictional teams completing tickets.
It permits different summary wording and confidence values, while checking the two names and counts, the generated line order, the exact input received by each child, and equality between each child's completed value and the collected score.

The review fixture describes a fictional uncontrolled treatment observation.
The parent selects numerical and causal claims and excludes a title-style claim.
The child receives its document and claim explicitly, while its review guidance comes from materialized parent state.
The observer checks that guidance's parent provenance and value, actual child count and inputs, confidence ranges, and returned-value preservation.
Different valid claim ordering and tool expressions are accepted.

The counter fixture follows the Ideal State's ten-call example.
An observer snapshots each child's inherited environment at entry, so merely guessing the final label list cannot satisfy the test.
It also checks the children's recorded input counters and terminal values.
Runtime validation of `list[str]` alone would not establish any of those semantic properties.

The observer composes with `DefaultContextBuilder` without changing the model's prompt content and records lifecycle callbacks through the public scoped API.
Its assertions run after execution, without prescribing inspection or evaluation tool-call sequences.

## Reproduction and limits

With `OPENAI_API_KEY` already configured, run:

```bash
NIGHTHAWK_OPENAI_INTEGRATION_TESTS=1 uv run pytest -v tests/integration/test_natural_composition.py -k sol
```

The module also parametrizes `gpt-5.6-luna`.
Its execution was not completed in this session: automatic approval review rejected that invocation because it could not confirm authorization for the new payloads, separately from the previously approved 13 integration tests.
The concurrently submitted Sol invocation was approved and completed successfully.
No Luna result is inferred from Sol or from the earlier 13-test suite.

This is one execution per workflow on one provider and model, without automatic whole-workflow retries.
It establishes working examples and regression oracles, not a reliability estimate, cross-backend equivalence, or general correctness of semantic judgments.
The tests do not settle the dedicated-inspection-tool versus inspection-helper comparison.
That comparison still requires controlled prompts, independent repetitions, additional task formulations, full interaction metrics, and separate evidence as specified in the [approved evaluation plan](../README.md#approved-inspection-helper-comparison).

Local artifacts from this execution are `/tmp/nighthawk-composition-sol.log` and `/tmp/nighthawk-composition-sol.xml`.
They contain pytest results and timings; they are not full model-interaction traces.
The API key was read without displaying or recording it.
