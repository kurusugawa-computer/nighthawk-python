import json


def get_assert(output: str, context: dict) -> dict:
    """Require the fixture calls while allowing equivalent positional or keyword syntax."""
    data = json.loads(output)
    expected_calls = context["config"]["expected_calls"]
    if not expected_calls:
        raise ValueError("expected_calls must specify at least one call")
    actual_calls = data.get("function_calls", [])
    missing = [call for call in expected_calls if call not in actual_calls]
    return {
        "pass": not missing,
        "score": 1 - len(missing) / len(expected_calls),
        "reason": f"Missing function calls: {missing!r}" if missing else "Required functions were called",
    }
