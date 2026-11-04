# Runtime boundary simplification regression check

## Decision and scope

Apply the workspace's scope, abstraction, ownership, dependency, and dead-concept practices to code and documentation.
Import runtime contracts and operations from their owning modules, keep executor protocols independent of the agent implementation, and separate tool result contracts from rendering.
Remove the built-in tool registration state, duplicate tool names, unused preparation state, and adapters that only forwarded calls.
Keep module-local implementation types private.
The retry predicate now runs once per matching failure, including the final attempt; exhaustion reporting no longer evaluates application policy a second time.

The production system prompt, tool descriptions, and input schemas are unchanged.
These runs check the refactoring through production execution; they are not a prompt experiment.

## Source provenance and protocol

Both runs start from uncommitted changes on base revision `2dc23b30e2f16a1ca3a8c6dafcf8d74158c94ebe`.
The source fingerprints identify the actual evaluated Python files and dependency declarations, rather than the unchanged base revision.

| Run | Source SHA-256 | Local artifact directory |
| --- | --- | --- |
| Interrupted candidate | `252feeca90acae9a32445631faf5973555fe0ce27a08a41b6b73e24415ed2070` | `evals/outputs/2026-11-04-code-practices` |
| Final candidate | `328ccd4c7ba4c67da2cbe443558f06d1a86448ed272e34542318c4faa8debaf7` | `evals/outputs/2026-11-04-code-practices-final` |

The shared suite selects 50 cases, each repeated three times with fresh fixture state, using `openai-responses:gpt-6-luna`, production defaults, and no evaluation settings overrides.
It covers explicit environments, model behavior, Natural composition, and value description.
The OpenAI provider integration suite runs separately with its repository-defined model settings.
Commands and artifact schemas are maintained in [CONTRIBUTING.md](../../CONTRIBUTING.md#model-evaluations-with-pytest).

Environment: Python 3.13.12, Nighthawk 0.18.0, Pydantic 2.14.2, Pydantic AI 2.63.0, OpenAI 3.41.0, MCP 2.3.0, Claude Agent SDK 0.2.175, pytest 9.2.0, and pytest-asyncio 1.4.1.

## Interrupted candidate

The deterministic suite caught a contract regression while the first live run was active: replacing `StepContext.environment` could change the type used to validate the committed output.
Preparation must retain the contracts selected at entry even when an executor replaces its working environment.
The final candidate retains that reference and passes the existing regression test.
The first live run was stopped to correct this deterministic failure; it was not restarted to obtain a passing model result.

| Passed | Failed | Interrupted | Not run | Observed requests | Input tokens | Output tokens | Summed trial seconds |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 101 | 1 | 1 | 47 | 209 | 196,590 | 10,737 | 390.43 |

The failing trial is `test_explicit_task_source_survives_python_shadowing`, repeat 1.
It commits the Python value 7 instead of the requested task value 100.
Its other two repeats pass.
The interrupted trial and all completed records remain in the original artifact directory.
Observed usage can omit a request that was in flight when the run stopped.

## Final shared-suite results

All 150 planned trials complete on the final candidate.
The source fingerprint still matches the working tree after the run.
Wall time is 886.07 seconds; summed trial time is reported separately below.

| Passed | Failed | Error | Observed requests | Input tokens | Output tokens | Summed trial seconds |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 148 | 2 | 0 | 483 | 417,165 | 20,662 | 885.72 |

| Case | Passed / trials | Failure |
| --- | ---: | --- |
| `test_explicit_task_source_survives_python_shadowing` | 2/3 | Repeat 3 commits the Python value 7 instead of the task value 100. |
| `test_resolved_return`, `value=0` | 2/3 | Repeat 3 submits `pass` instead of returning the value. |
| Each of the other 48 cases | 3/3 | None. |

In the zero-return failure, the model calls `nh_assign` to retain 0 in a temporary named `output`, then explicitly submits `{"result":{"kind":"pass"}}`.
The recorded runtime outcome is that submitted `Pass`; it is not a misinterpreted `Return(0)`.
The failed records are `trial-0003.json` and `trial-0062.json` in the final artifact directory.
Both failures remain in the reported results; neither received an additional success-seeking retry.

## Deterministic and distribution verification

- Ruff lint and formatting checks pass; Pyright reports no errors or warnings.
- The final deterministic suite passes 1,354 tests, with 79 opt-in or environment-specific skips, in 45.67 seconds.
  It includes documentation integrity, executable examples, strict documentation builds, and rendered-link checks.
- The source distribution and wheel build successfully, and the wheel installs into an independent Python 3.13 environment.
  All 11 installed-distribution and documentation-example tests pass in 5.21 seconds.
  Installed Python file contents match the final source tree.
- OpenAI provider integration passes 10 tests, with 64 other opt-in cases skipped, in 51.94 seconds.

## Limitations

Three trials per case provide limited evidence about model reliability.
The same-name task lookup failure is also recorded in the [API consolidation evaluation](2026-10-31-api-consolidation.md); this change does not establish that the model can reliably select that source.
The zero-return failure also leaves requested control flow subject to model judgment errors.
There is no unchanged live control in this check, so differences from historical pass counts do not establish an improvement or regression in model quality.
Coding-agent backends, optional image cases, and Python 3.14/3.15 were not run locally.
Synthetic success does not establish application-level judgment quality or a security boundary.
