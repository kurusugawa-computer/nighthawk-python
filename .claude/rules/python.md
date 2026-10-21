---
paths:
  - "**/*.py"
---

# Comments and docstrings

Write a comment or docstring only for a fact that the code, names, signatures, types, specification, and tests do not convey.

## Write

- Why a choice was made, especially an intentionally non-obvious one.
- Upstream library behavior that the code depends on or works around.
- Platform, process, or environment constraints.
- Concurrency, cancellation, ordering, and lifetime invariants.

## Do not write

- Restatements of what the code does, including step-by-step narration.
- Section dividers or heading comments; group code with classes, modules, or name prefixes instead.
- Planning Ids, phases, or history ("used to", "now").
- Citations of specification section numbers or test file paths; tests pin specified behavior.
- Type information already present in annotations.

## Form

- Keep each comment and docstring compact; prefer one line.
- Use semantic line breaks.

## Text that is not a comment

The following text is consumed at runtime or serves as test data.
Change it only as a deliberate behavior change:

- Natural docstrings (`"""execute ...`).
- Docstrings of binding functions and tools, which LLMs may read.
- Docstrings of Pydantic models that enter LLM-facing JSON schemas, such as `ModelStepResponse`.
- Fixture docstrings that tests assert on or expose to Natural blocks.

Tool directives (`noqa`, `type: ignore`, `pyright: ignore`) are outside these rules.
