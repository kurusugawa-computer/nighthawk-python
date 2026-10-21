# Retire completed tool experiments

The September tool-interface comparisons have recorded results and a selected production interface.
There is no active comparison using the `original`, `refined`, `merged`, or dedicated-inspection treatments.
Keeping their executable implementations required adapting old fixture contracts to unrelated Core changes while retaining no current regression purpose.
The maintained adapter had already diverged from the runtime and source snapshots used for the recorded trials.

Remove the prototype, fixture builder, study adapter, command-line runners, report generator, and tests of those experiment-specific contracts.
Their final maintained source is available at Git revision `656df7e`; the dated study manifests identify the source snapshots actually used for measured runs.
The assessments, trial metrics, failure traces, configurations, and manifests remain unchanged.
This cleanup makes no new model-quality claim and does not reinterpret previous outcomes.

Current runtime coverage remains with its behavior owners:

- `tests/runtime/test_environment.py` and `test_discovery.py`: lexical lookup, unbound names, collisions, qualified references, live values, and discovery continuations.
- `tests/tools/`: production tool execution, ordering, rendering, and discovery content.
- `tests/oversight/` and `tests/lifecycle/`: admission, rejection, validation, partial effects, cancellation, and completion.
- `tests/integration/test_natural_composition.py`: actual Natural child calls and inherited policy, including the ten-call counter case.
- `evals/promptfoo/` and `tests/evals/`: production-backed model regression fixtures and their adapter/assertion tests.

A new interface investigation should state its decision, control, and current contract, rather than treating an archived experiment's adapter as a runtime obligation.
