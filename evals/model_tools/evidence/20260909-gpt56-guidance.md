# Model-facing tool experiment

Completed trials: 192

Prototype experiment on synthetic tasks; not a production-runtime benchmark.
Wilson intervals below are descriptive trial-level intervals; repeated task formulations are correlated.

Runtime operation counts exclude the structured completion tool and schema-rejected calls that never entered the workspace. Error counts include intentional error scenarios.

| Model | Surface | Guidance | Pass | State/effects | 95% interval | Runtime operations/trial | Observed errors | Duplicate domain calls | Input tokens | Output tokens | Seconds/trial |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| openai:gpt-5.6-luna | merged | examples | 16/16 | 16/16 | 80.6%-100.0% | 4.94 | 2 | 0 | 142400 | 4505 | 10.84 |
| openai:gpt-5.6-luna | merged | short | 15/16 | 16/16 | 71.7%-98.9% | 4.38 | 2 | 0 | 101463 | 3905 | 9.29 |
| openai:gpt-5.6-luna | original | examples | 16/16 | 16/16 | 80.6%-100.0% | 6.31 | 15 | 0 | 129405 | 5762 | 12.81 |
| openai:gpt-5.6-luna | original | short | 16/16 | 16/16 | 80.6%-100.0% | 5.06 | 2 | 0 | 108016 | 4643 | 9.82 |
| openai:gpt-5.6-luna | refined | examples | 14/16 | 16/16 | 64.0%-96.5% | 4.25 | 2 | 0 | 106361 | 3950 | 10.93 |
| openai:gpt-5.6-luna | refined | short | 16/16 | 16/16 | 80.6%-100.0% | 4.50 | 4 | 0 | 102905 | 3754 | 10.57 |
| openai:gpt-5.6-sol | merged | examples | 16/16 | 16/16 | 80.6%-100.0% | 4.94 | 1 | 0 | 126031 | 3589 | 14.37 |
| openai:gpt-5.6-sol | merged | short | 16/16 | 16/16 | 80.6%-100.0% | 4.31 | 2 | 0 | 93638 | 3059 | 12.05 |
| openai:gpt-5.6-sol | original | examples | 16/16 | 16/16 | 80.6%-100.0% | 5.44 | 7 | 0 | 105188 | 3979 | 14.29 |
| openai:gpt-5.6-sol | original | short | 16/16 | 16/16 | 80.6%-100.0% | 4.94 | 2 | 0 | 98388 | 3843 | 12.44 |
| openai:gpt-5.6-sol | refined | examples | 16/16 | 16/16 | 80.6%-100.0% | 4.31 | 1 | 0 | 96830 | 2997 | 11.76 |
| openai:gpt-5.6-sol | refined | short | 16/16 | 16/16 | 80.6%-100.0% | 4.38 | 2 | 0 | 93415 | 3037 | 12.37 |

## Failures

- openai:gpt-5.6-luna / merged / short / assignment / phrasing 0 / repetition 0: Expected outcome pass, got return
- openai:gpt-5.6-luna / refined / examples / nested / phrasing 0 / repetition 1: Expected outcome pass, got return
- openai:gpt-5.6-luna / refined / examples / discovery / phrasing 0 / repetition 1: Expected outcome pass, got return

## Configuration

```json
{
  "models": [
    "openai:gpt-5.6-luna",
    "openai:gpt-5.6-sol"
  ],
  "surfaces": [
    "original",
    "refined",
    "merged"
  ],
  "guidance": [
    "short",
    "examples"
  ],
  "cases": [
    "assignment",
    "mutation",
    "collision",
    "nested",
    "return",
    "continue",
    "discovery",
    "opaque"
  ],
  "repetitions": 2,
  "phrasings": 1,
  "concurrency": 4,
  "timeout": 300.0,
  "model_timeout": 120.0,
  "max_output_tokens": 8192,
  "total_token_limit": 100000,
  "reasoning_effort": "medium",
  "seed": 20260909,
  "output": "evals/model_tools/outputs/gpt56-guidance-20260909",
  "started": "2026-09-09T18:04:22.354866+00:00",
  "trials": 192,
  "pydantic_ai_version": "2.37.0",
  "model_settings": {
    "max_tokens": 8192,
    "timeout": 120.0,
    "openai_reasoning_effort": "medium"
  },
  "source_sha256": {
    "run.py": "a01c264fcb807bd49ad4181d2bcf5ddd151661789d10d7c034fbba22cbe6b41c",
    "__init__.py": "65575949f9d65221bfe1b342800e44a13c656d90fa5dbb1ecfae05d7e3cb0b92",
    "cases.py": "52db764f145c5d5885eb71e060ad885cd465a734a9d3ad05891197c5d1ebc892",
    "prototype.py": "1799bde71170beb415492a11dc0822d43558d9ff2ff0300714273b9282b5c4d7",
    "report.py": "cb9bdc37ceeba0fc5f6ae1f6ed81da4ff2bd435b5b4ce083b8dca4dada3d942c"
  }
}
```

## Task breakdown

Each entry is overall passes / state-and-effect passes / trials.

| Case | merged | original | refined |
| --- | --- | --- | --- |
| assignment | 7 / 8 / 8 | 8 / 8 / 8 | 8 / 8 / 8 |
| collision | 8 / 8 / 8 | 8 / 8 / 8 | 8 / 8 / 8 |
| continue | 8 / 8 / 8 | 8 / 8 / 8 | 8 / 8 / 8 |
| discovery | 8 / 8 / 8 | 8 / 8 / 8 | 7 / 8 / 8 |
| mutation | 8 / 8 / 8 | 8 / 8 / 8 | 8 / 8 / 8 |
| nested | 8 / 8 / 8 | 8 / 8 / 8 | 7 / 8 / 8 |
| opaque | 8 / 8 / 8 | 8 / 8 / 8 | 8 / 8 / 8 |
| return | 8 / 8 / 8 | 8 / 8 / 8 | 8 / 8 / 8 |

## Matched comparisons

Counts are left wins / ties / right wins, matched by model, task, wording, guidance, and repetition.

| Left | Right | Metric | Wins / ties / losses |
| --- | --- | --- | --- |
| refined | original | passed | 0 / 62 / 2 |
| refined | original | state_passed | 0 / 64 / 0 |
| merged | refined | passed | 2 / 61 / 1 |
| merged | refined | state_passed | 0 / 64 / 0 |

## Failure diagnostics

- merged: 1 wrong-control-only failures; inspection used in 8/8 collision trials; operation errors {'ValueError': 7}.
- original: 0 wrong-control-only failures; inspection used in 0/8 collision trials; operation errors {'NameError': 24, 'SyntaxError': 2}.
- refined: 2 wrong-control-only failures; inspection used in 8/8 collision trials; operation errors {'ValueError': 7, 'SyntaxError': 2}.
