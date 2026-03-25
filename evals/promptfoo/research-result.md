# Research: Evaluating Nighthawk Natural Block Execution with promptfoo

## 1. What is promptfoo?

promptfoo is an open-source LLM evaluation framework that enables systematic testing of prompts, models, and LLM-powered applications. It provides:

- **Declarative configuration** via `promptfooconfig.yaml`
- **Multiple assertion types** (deterministic, model-graded, similarity, custom)
- **Custom providers** (Python, JavaScript, HTTP, shell)
- **CLI tooling** (`promptfoo eval`, `promptfoo view`)
- **CI/CD integration** (GitHub Actions, GitLab CI)
- **Red teaming** for LLM security testing

Core workflow: define prompts, providers, and test cases in YAML; run `promptfoo eval`; review results in a web UI or as JSON/CSV/HTML.

## 2. promptfoo core concepts

### 2.1 Configuration structure

```yaml
description: "My evaluation"

prompts:
  - "Translate {{text}} to {{language}}"    # inline
  - file://prompts/translate.txt            # file reference

providers:
  - openai:gpt-4o                           # built-in provider
  - file://my_provider.py                   # custom Python provider

defaultTest:
  assert:
    - type: cost
      threshold: 0.01

tests:
  - vars:
      text: "Hello"
      language: "French"
    assert:
      - type: contains
        value: "Bonjour"
      - type: llm-rubric
        value: "Output is a natural French translation"

outputPath: results.json
```

### 2.2 Providers

promptfoo supports built-in providers (OpenAI, Anthropic, Google, Azure, etc.) and custom providers. Custom Python providers are particularly relevant for Nighthawk integration.

### 2.3 Test cases and variables

Each test case specifies `vars` (template variables substituted into prompts) and optional `assert` (assertions to evaluate the output). Array values in vars create a combinatorial matrix of test cases.

### 2.4 Assertions

Assertions validate LLM outputs. They support negation (`not-` prefix) and thresholds. Categories:

- **Deterministic**: `contains`, `equals`, `regex`, `is-json`, `javascript`, `python`, `cost`, `latency`
- **Model-graded**: `llm-rubric`, `model-graded-closedqa`, `factuality`, `g-eval`, `answer-relevance`
- **Similarity**: `similar` (embedding-based), `levenshtein`, `rouge-n`
- **Structural**: `is-json`, `contains-json`, `is-valid-function-call`

## 3. Key assertion types for Nighthawk evaluation

### 3.1 Deterministic assertions

| Type | Use case for Nighthawk |
|------|------------------------|
| `equals` | Exact match of binding values (e.g., `v == 15`) |
| `contains` / `icontains` | Output includes expected substring |
| `is-json` / `contains-json` | Validate structured JSON outcome |
| `regex` | Pattern matching on output text |
| `javascript` / `python` | Custom validation logic (inspect bindings, outcome kind) |
| `cost` | Ensure inference stays within budget |
| `latency` | Ensure response time is acceptable |

### 3.2 Model-graded assertions

| Type | Use case for Nighthawk |
|------|------------------------|
| `llm-rubric` | General quality evaluation ("Did the LLM correctly interpret the Natural block?") |
| `model-graded-closedqa` | Closed-domain QA evaluation against a reference answer |
| `factuality` | Verify factual consistency of returned values |
| `g-eval` | Chain-of-thought evaluation with fine-grained scoring |

### 3.3 Custom Python assertions

```yaml
assert:
  - type: python
    value: |
      import json
      result = json.loads(output)
      return result.get("kind") == "pass" and result.get("bindings", {}).get("v") == 15
```

Or via external file:

```yaml
assert:
  - type: python
    value: file://assertions/check_binding.py
```

The Python assertion function receives:
- `output` (str): The LLM response
- `context` (dict): Contains `vars`, `prompt`, `config`, `providerResponse`

Return types: `bool`, `float` (score), or `dict` with `pass`, `score`, `reason`.

## 4. Custom Python provider for Nighthawk

### 4.1 Provider concept

A custom Python provider wraps a Nighthawk Natural function execution as a promptfoo provider. This is the key integration point.

### 4.2 Provider function signature

```python
# nighthawk_provider.py

def call_api(prompt: str, options: dict, context: dict) -> dict:
    """
    Parameters:
        prompt:   The prompt text (or JSON-encoded conversation)
        options:  Provider configuration from YAML
                  options["config"] contains custom fields
        context:  Test case information
                  context["vars"] contains test variables

    Returns:
        {
            "output": str | dict,     # Required: the LLM response
            "tokenUsage": {           # Optional
                "total": int,
                "prompt": int,
                "completion": int
            },
            "cost": float,            # Optional
            "latencyMs": int,         # Optional
            "error": str              # Optional: error description
        }
    """
```

### 4.3 Nighthawk provider implementation pattern

```python
# promptfoo_provider.py

import nighthawk as nh
import json


def call_api(prompt: str, options: dict, context: dict) -> dict:
    """Execute a Nighthawk Natural function and return its result."""
    config = options.get("config", {})
    model = config.get("model", "openai-responses:gpt-4o-mini")
    test_vars = context.get("vars", {})

    # Build the step executor
    step_executor = nh.AgentStepExecutor.from_configuration(
        configuration=nh.StepExecutorConfiguration(model=model),
    )

    # Define and execute the Natural function dynamically
    # (or import a pre-defined one from the application)
    try:
        with nh.run(step_executor):
            # Import the target function from the application
            from my_app import my_natural_function

            result = my_natural_function(**test_vars)

        return {
            "output": json.dumps({"result": result}),
        }
    except Exception as e:
        return {
            "output": "",
            "error": str(e),
        }
```

### 4.4 promptfoo configuration for Nighthawk

```yaml
# promptfooconfig.yaml

description: "Nighthawk Natural block evaluation"

prompts:
  - "{{natural_program}}"   # The Natural block text as the prompt

providers:
  - id: file://promptfoo_provider.py
    label: "Nighthawk (gpt-4o-mini)"
    config:
      model: "openai-responses:gpt-4o-mini"
  - id: file://promptfoo_provider.py
    label: "Nighthawk (gpt-4o)"
    config:
      model: "openai-responses:gpt-4o"

tests:
  - description: "Simple arithmetic binding"
    vars:
      natural_program: "Set <:v> to <v> + 5."
      v: 10
    assert:
      - type: python
        value: |
          import json
          result = json.loads(output)
          return result["result"] == 15

  - description: "Conditional return"
    vars:
      natural_program: "if <v> >= 10 then return 11 else <:v> = <v> + 5"
      v: 9
    assert:
      - type: python
        value: |
          import json
          result = json.loads(output)
          return result["result"] == 11

  - description: "Exception raising"
    vars:
      natural_program: 'raise a <ValueError> with message "test error"'
    assert:
      - type: python
        value: |
          import json
          result = json.loads(output)
          return result.get("error_type") == "ValueError"

  - description: "Output quality"
    vars:
      natural_program: "Summarize <text> in one sentence."
      text: "Long article text here..."
    assert:
      - type: llm-rubric
        value: "The output is a concise, accurate one-sentence summary"
        threshold: 0.8
```

## 5. Integration approaches

### 5.1 Approach A: Custom provider wrapping Natural functions (recommended)

**Concept**: Write a Python provider that imports and executes Nighthawk Natural functions, returning their results to promptfoo for assertion evaluation.

**Advantages**:
- End-to-end testing of the full Nighthawk execution pipeline
- Side-by-side model comparison (test the same Natural block with different LLM providers)
- promptfoo handles test case management, assertion evaluation, and reporting
- Supports both deterministic and model-graded assertions

**Architecture**:
```
promptfooconfig.yaml
    |
    v
promptfoo eval
    |
    v
promptfoo_provider.py (call_api)
    |
    v
nighthawk.run(step_executor) -> natural_function(**vars)
    |
    v
LLM API (OpenAI, Anthropic, etc.)
    |
    v
Result -> promptfoo assertions
```

**File structure**:
```
project/
  promptfooconfig.yaml
  promptfoo_provider.py
  assertions/
    check_bindings.py
    check_outcome.py
  test_cases/
    arithmetic.yaml
    summarization.yaml
```

### 5.2 Approach B: Hybrid with pytest

**Concept**: Use promptfoo for LLM output quality evaluation while keeping pytest for deterministic unit tests (using `ScriptedExecutor`).

**Advantages**:
- Deterministic tests remain fast and free (no LLM calls)
- promptfoo handles only the non-deterministic LLM quality evaluation
- Leverages existing `nighthawk.testing` infrastructure

**Architecture**:
```
pytest (deterministic)                  promptfoo (LLM quality)
  |                                       |
  v                                       v
ScriptedExecutor / CallbackExecutor     promptfoo_provider.py
  |                                       |
  v                                       v
No LLM calls                           Real LLM calls
  |                                       |
  v                                       v
assert statements                       llm-rubric, python assertions
```

### 5.3 Approach C: Prompt-level evaluation (lightweight)

**Concept**: Directly evaluate the system/user prompts that Nighthawk constructs, without running the full Natural function pipeline. Useful for testing prompt template quality.

**Architecture**:
```
promptfooconfig.yaml
    |
    v
prompts: system + user prompt templates
    |
    v
providers: openai:gpt-4o (direct)
    |
    v
assertions on raw LLM output (before Nighthawk parsing)
```

## 6. Assertion strategy for Natural block evaluation

### 6.1 Binding correctness (deterministic)

Verify that write bindings (`<:name>`) receive correct values:

```yaml
assert:
  - type: python
    value: |
      import json
      data = json.loads(output)
      expected = context["vars"].get("expected_result")
      return data["result"] == expected
```

### 6.2 Outcome kind correctness (deterministic)

Verify the correct StepOutcome kind (pass, return, raise, break, continue):

```yaml
assert:
  - type: python
    value: |
      import json
      data = json.loads(output)
      return data["outcome_kind"] == context["vars"]["expected_kind"]
```

### 6.3 Output quality (model-graded)

For open-ended Natural blocks (summarization, classification, generation):

```yaml
assert:
  - type: llm-rubric
    value: |
      The output correctly follows the Natural block instruction.
      It should be concise, accurate, and directly address the request.
    threshold: 0.7
  - type: model-graded-closedqa
    value: "The answer matches the expected behavior described in the test case"
```

### 6.4 Cost and latency (deterministic)

```yaml
assert:
  - type: cost
    threshold: 0.05     # Max $0.05 per call
  - type: latency
    threshold: 10000    # Max 10 seconds
```

### 6.5 Structural validation (deterministic)

```yaml
assert:
  - type: is-json
  - type: python
    value: |
      import json
      data = json.loads(output)
      return "result" in data and "outcome_kind" in data
```

## 7. CI/CD integration

### 7.1 GitHub Actions workflow

```yaml
# .github/workflows/llm-eval.yml

name: LLM Evaluation
on:
  pull_request:
    paths:
      - "src/nighthawk/**"
      - "promptfooconfig.yaml"
      - "promptfoo_provider.py"

jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - uses: actions/setup-node@v4
        with:
          node-version: "22"

      - name: Install dependencies
        run: |
          pip install uv
          uv sync --all-extras --all-groups

      - name: Cache promptfoo
        uses: actions/cache@v4
        with:
          path: ~/.cache/promptfoo
          key: promptfoo-${{ hashFiles('promptfooconfig.yaml') }}

      - name: Run evaluation
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          PROMPTFOO_CACHE_TTL: 86400
        run: npx promptfoo@latest eval -c promptfooconfig.yaml -o results.json

      - name: Check for failures
        run: |
          failures=$(jq '.results.stats.failures' results.json)
          if [ "$failures" -gt 0 ]; then
            echo "LLM evaluation failed with $failures failures"
            exit 1
          fi

      - name: Upload results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: promptfoo-results
          path: results.json
```

### 7.2 Running locally

```bash
# Install promptfoo
npm install -g promptfoo

# Initialize configuration
promptfoo init

# Run evaluation
promptfoo eval -c promptfooconfig.yaml

# View results in browser
promptfoo view

# Export results
promptfoo eval -c promptfooconfig.yaml -o results.json
promptfoo eval -c promptfooconfig.yaml -o results.html
promptfoo eval -c promptfooconfig.yaml -o results.csv
```

## 8. Considerations for Nighthawk

### 8.1 Challenges

1. **Non-determinism**: LLM outputs vary between runs. Assertions must tolerate variance while catching regressions. Use `threshold` on model-graded assertions and run multiple trials.

2. **Execution pipeline complexity**: Nighthawk Natural blocks go through AST transformation, binding resolution, prompt construction, LLM execution, tool calls, and outcome parsing. The provider must orchestrate this full pipeline.

3. **Tool call evaluation**: Natural blocks may invoke `nh_assign`, `nh_eval`, or user-defined tools. The provider must capture these interactions for assertion.

4. **State dependencies**: Natural blocks depend on Python locals/globals context. The provider must set up realistic execution contexts.

5. **Cost management**: Running many test cases against real LLMs is expensive. Use caching (`PROMPTFOO_CACHE_TTL`), cheaper models for development, and limit concurrency.

### 8.2 Recommendations

1. **Start with Approach B (Hybrid)**: Keep deterministic tests in pytest with `ScriptedExecutor`; use promptfoo only for LLM quality evaluation of representative test cases.

2. **Design the provider to return structured data**: Include `result`, `outcome_kind`, `bindings`, `tool_calls`, and `error` in the provider output so assertions can inspect each aspect.

3. **Use model-graded assertions sparingly**: Reserve `llm-rubric` for genuinely open-ended evaluations. Prefer deterministic assertions (`python`, `equals`) for binding correctness.

4. **Implement model comparison**: Configure multiple providers (e.g., gpt-4o-mini vs gpt-4o) to evaluate cost/quality trade-offs for Natural block execution.

5. **Cache aggressively in CI**: Set `PROMPTFOO_CACHE_TTL` to avoid redundant LLM calls across identical test cases.

6. **Separate test suites**: Create separate promptfoo configs for different evaluation dimensions (correctness, quality, performance, security).

### 8.3 Provider output schema recommendation

Design the custom provider to return a structured JSON output that enables rich assertions:

```python
{
    "result": <value>,           # The final return value or binding values
    "outcome_kind": "pass",      # StepOutcome kind
    "bindings": {                # All write binding values
        "v": 15,
        "summary": "..."
    },
    "tool_calls": [              # Tools invoked during execution
        {"name": "nh_assign", "args": {"target_path": "v", "expression": "10 + 5"}}
    ],
    "error": null,               # Error message if raised
    "error_type": null           # Error type if raised
}
```

## 9. Red teaming Natural blocks

promptfoo's red team capabilities can test Natural blocks for:

- **Prompt injection**: Can adversarial input in read bindings (`<name>`) manipulate the LLM to produce unintended outcomes?
- **Excessive agency**: Does the LLM invoke tools (`nh_eval`, user-defined tools) beyond what the Natural block intends?
- **Information leakage**: Does the LLM expose system prompt content or internal state through bindings?

Note: Nighthawk's design treats Natural DSL sources as trusted, repository-managed assets (per CLAUDE.md). Red teaming is relevant when read binding values originate from external input that flows into the Natural block context.

## 10. Summary

| Aspect | Recommendation |
|--------|---------------|
| Primary integration | Custom Python provider wrapping Natural functions |
| Deterministic tests | Keep in pytest with `ScriptedExecutor` |
| Quality evaluation | `llm-rubric` and `python` assertions in promptfoo |
| Model comparison | Multiple provider entries in promptfooconfig.yaml |
| CI/CD | GitHub Actions with caching and failure gates |
| Cost control | Cache, cheap models for dev, limit concurrency |
| Red teaming | Use for Natural blocks that process external input |

## References

- [promptfoo documentation](https://www.promptfoo.dev/docs/intro/)
- [Python provider](https://www.promptfoo.dev/docs/providers/python/)
- [Assertions and metrics](https://www.promptfoo.dev/docs/configuration/expected-outputs/)
- [Model-graded assertions](https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/)
- [LLM rubric](https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/llm-rubric/)
- [Python assertions](https://www.promptfoo.dev/docs/configuration/expected-outputs/python/)
- [Testing LLM chains](https://www.promptfoo.dev/docs/configuration/testing-llm-chains/)
- [CI/CD integration](https://www.promptfoo.dev/docs/integrations/ci-cd/)
- [GitHub Actions](https://www.promptfoo.dev/docs/integrations/github-action/)
- [Red teaming](https://www.promptfoo.dev/docs/red-team/)
- [CLI reference](https://www.promptfoo.dev/docs/usage/command-line/)
- [Configuration guide](https://www.promptfoo.dev/docs/configuration/guide/)
