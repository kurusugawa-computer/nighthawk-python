# Explicit request schemas for capability construction

## Decision

When a Natural block constructs a capability's structured request, expose the request schema explicitly through ordinary Python, such as a read binding to `FilePatchRequest.model_json_schema`.
Static callable descriptions do not evaluate Pydantic's deferred constructor signatures, which Pydantic exposes through `LazyClassAttribute`, so supplying a type binding does not display its fields.
Do not add automatic descriptor evaluation or another capability-description protocol.
The [Agent guide](../../docs/agent/index.md#compose-reusable-parts) and `tests/integration/harness/test_file_patch.py` demonstrate this composition.

## Case and settings

The case requires a single `apply_file_patch` capability invocation that adds a file and then updates it in the same request.
Assertions inspect the returned operation count, actual final file text, successful capability invocation, and perimeter completion.
It uses `openai-responses:gpt-6-luna`, `openai_reasoning_effort="low"`, an eight-request allocation, and a 30,000-token allocation.
The writable root is a fresh pytest temporary directory; the explicitly selected in-memory audit store makes no durability claim.

## Results

Three different authored programs were each executed once, with unchanged effect and result assertions:

| Program | Result | Observed failure or outcome |
| --- | --- | --- |
| Capability name and task only | Failed, 2.82 seconds | Guessed `op`/`path` fields and an extra top-level `target`; request validation rejected the call. |
| Explicit `FilePatchRequest`, `AddFileOperation`, and `UpdateFileOperation` read bindings | Failed, 2.95 seconds | Constructed `AddFileOperation(path=...)`; required `file_path` was missing. |
| Explicit call to `FilePatchRequest.model_json_schema()` through a read binding | Passed, 9.05 seconds | One successful two-operation patch; final text and recorded effects matched. |

The passing execution observed four requests, 4,276 input tokens, 159 output tokens, and 4,435 total tokens.
The first two failures remain failures; the revised program is not a retry of either unchanged program.
One successful sample does not establish reliability, and these cases do not establish security isolation or durable auditing.
Codex, Claude, image cases, and other Python versions were not exercised.

## Source and environment

The trials ran on an unpublished development revision; the fingerprints below identify the exercised sources.
The adapter implementation was unchanged during all three trials; only the test's imported types and Natural instructions changed.
The retained test differs from the passing trial only in import formatting and an unused-import lint directive for its Natural read binding.
Python 3.13.12, Nighthawk 0.19.0, Pydantic 2.14.2, Pydantic AI 2.63.0, OpenAI 3.41.0, HTTPX 0.28.1, pytest 9.2.0.

| File | SHA-256 |
| --- | --- |
| `src/nighthawk/adapters/files.py` | `3c54ce479f7e1bb4e11342eab16adecdd5b237a4f196fa6651d0961c0c4eded2` |
| `src/nighthawk/parts/files.py` | `82b8f5d42bcad31901b61d61ec38868a80bcee6d2af94097ebe03d73db261121` |
| `tests/integration/agent/test_files.py` | `3e65a2c2df3788230e7349fd9a08e234a272a2bb14f2eb583efc1f1121df2d3b` |
