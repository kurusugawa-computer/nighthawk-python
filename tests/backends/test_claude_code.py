from __future__ import annotations

from dataclasses import dataclass

from nighthawk.backends.claude_code import _serialize_result_message_to_json


@dataclass
class _DataclassResultMessage:
    is_error: bool
    result: str | None
    usage: dict[str, int]


class _ModelDumpJsonResultMessage:
    def model_dump_json(self) -> str:
        return '{"is_error": true, "result": "failed"}'


def test_serialize_result_message_to_json_handles_dataclass() -> None:
    result_message = _DataclassResultMessage(is_error=True, result="failed", usage={"input_tokens": 1})

    result_message_json = _serialize_result_message_to_json(result_message)

    assert '"is_error": true' in result_message_json
    assert '"result": "failed"' in result_message_json
    assert '"input_tokens": 1' in result_message_json


def test_serialize_result_message_to_json_uses_model_dump_json_when_available() -> None:
    result_message_json = _serialize_result_message_to_json(_ModelDumpJsonResultMessage())

    assert result_message_json == '{"is_error": true, "result": "failed"}'
