# Prompt cache boundaries

## Decision and scope

When `execution_model_settings` enables Pydantic AI's `cache` setting, the default presentation places a `CachePoint` after the Natural program text and at each `PROGRAM_CACHE_BOUNDARY` marker inside it.
Without `cache`, the markers are removed and the request is unchanged.
This record supports the documented claim that repeated calls of one Natural function can then read the program prefix from the provider's cache.

## Evaluation protocol

The case `test_repeated_calls_route_each_ticket_with_one_stable_program` calls one Natural function four times in a Python loop.
Its program holds 24 routing rules (1,271 tokens in `o200k_base`) before a boundary marker, followed by one instruction.
Each trial routes four synthetic tickets, each prefixed with a fresh random reference, so that no request repeats an earlier trial and only the program prefix can be shared.

| Arm | `execution_model_settings` | Run directory |
| --- | --- | --- |
| Off | `{}` (production default) | `20261109-prompt-cache-boundaries-off-2` |
| On | `{"cache": true}` | `20261109-prompt-cache-boundaries-on-2` |

- Source: `6d1ce48` with the change described here, before commit (source fingerprint `375b9f2852bb55fd`).
- Model: `openai-responses:gpt-6-luna`; three trials per arm; the arms ran one after the other from 2026-11-09 19:41 UTC, for 80 seconds (off) and 102 seconds (on).
- Versions: Python 3.13.12, Pydantic 2.14.2, Pydantic AI Slim 2.63.0, OpenAI SDK 3.41.0, pytest 9.2.0.

## Results

Both arms passed 3/3: every ticket was routed to the expected team.
Each call used two requests.
The first request of each call carries the program prefix; the second request reads its predecessor within the call in both arms.

| Arm | First requests that read the cache | Tokens read by such a request | Requests | Input tokens | Cache-read input tokens | Output tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Off | 0 of 12 | 0 | 24 | 47,436 | 21,720 | 696 |
| On | 11 of 12 | 1,740 | 24 | 47,493 | 40,876 | 721 |

- With the cache points, every first request except the first call of the first trial read the 1,740-token prefix through the program.
  Without them, no first request read the cache, although the prefix was identical: the provider reuses a prefix only at a cache point placed at the same position.
- Cache-write tokens of a first request fell from about 1,810 to about 70 (the differing bindings) once the prefix was cached.

## Pilot and its confounds

A pilot of the same comparison (`20261109-prompt-cache-boundaries-off` and `-on`, source fingerprint `0c91bdb20e572a5e`) used a 512-token rule list and the same four tickets in every trial; both arms passed 3/3.
Its program prefix stayed near the provider's 1,024-token minimum, so the first trial's cache points rarely produced reads, and repeated trials sent requests identical to earlier ones, which the provider reused in full in both arms (for example 1,041 cache-read tokens on a first request without cache points).
The case was revised to the longer rule list and fresh ticket references before the runs above.

## Interpretation and limits

Cache points let calls that share a program reuse its prefix; the gain grows with the program's share of each request and with the number of calls.
The evidence covers one provider and model, whose explicit breakpoints require a cache point at the same position in the later request; other providers differ, and those without request-side cache points ignore them.
Cache writes are billed above uncached input, so the setting stays with the host.
The case is synthetic, and three trials per arm do not establish reliability or cost on other workloads.
Usage is completed-agent usage, not a billing record.

## Artifacts and verification

Run directories under ignored `evals/outputs/`: `20261109-prompt-cache-boundaries-off-2`, `20261109-prompt-cache-boundaries-on-2`, and the pilot `20261109-prompt-cache-boundaries-off` and `20261109-prompt-cache-boundaries-on`.
Per-request usage is in the model transcripts of each trial record.
