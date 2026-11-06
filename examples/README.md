# Reference applications

These runnable applications demonstrate public Nighthawk contracts and application-owned policy, storage, evaluation, and presentation.
They live outside the installed library and use the same Core, Harness, Parts, and Session APIs available to other applications.
The [composition guide](../docs/agent/composition.md) explains the workflows; [Sessions](../docs/agent/sessions.md) covers embedding.

| Example | Composition and observable result |
| --- | --- |
| [procedure_discovery.py](procedure_discovery.py) | Select a retained procedure, resolve it deterministically, and invoke it within a narrowed grant. |
| [async_object_workflow.py](async_object_workflow.py) | Compose typed asynchronous branches while retaining successful findings alongside ordinary failures. |
| [learning_loop.py](learning_loop.py) | Pass observed failures into an explicit learning judgment and use its output in a subsequent proposal. |
| [retained_text_workflow.py](retained_text_workflow.py) | Use the conversation's source-generation part without a Session, then evaluate and reuse one retained definition in fresh batch runs. |
| [continued_object_workflow.py](continued_object_workflow.py) | Save selected state and evidence, then continue pending work in a fresh run. |
| [evaluate_object_workflow.py](evaluate_object_workflow.py) | Measure correctness, evidence coverage, unresolved work, and available resource observations on a fixed task set. |
| [durable_audit.py](durable_audit.py) | Persist perimeter attempts and completions in application-selected SQLite storage and inspect indeterminate attempts after restart. |
| [repl/](repl/README.md) | Embed the library in a persistent interactive application with explicit bindings and inspectable execution. |

## Running the applications

Use Python 3.13+ and install the checkout's dependencies with `uv sync --all-extras --all-groups`.
Supply a configured `Runner` as shown in the composition guide.
Launch the optional terminal with `uv run python -m examples.repl`; its [README](repl/README.md) owns keyboard, process, and factory configuration.

## Verification

Run deterministic application checks with `uv run pytest -q tests/examples/`.
Run provider-backed composition checks with:

```sh
NIGHTHAWK_OPENAI_INTEGRATION_TESTS=1 uv run --env-file .env pytest -q -s tests/integration/harness/test_reference_applications.py
```

Follow [Integration tests](../CONTRIBUTING.md#integration-tests) for provider setup and test selection.
