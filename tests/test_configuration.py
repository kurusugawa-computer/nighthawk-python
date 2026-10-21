from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError
from pydantic_ai.models.function import FunctionModel
from pydantic_core import PydanticSerializationError

import nighthawk as nh


class SensitiveModel(FunctionModel):
    def __repr__(self) -> str:
        raise AssertionError("Synthetic credential representation must not be evaluated")


def model_named(name: str) -> FunctionModel:
    return SensitiveModel(lambda messages, information: None, model_name=name)  # type: ignore[arg-type]


def test_live_model_identity_and_nested_serialization_barrier() -> None:
    model = model_named("unknown-local-model")
    configuration = nh.StepExecutorConfiguration(execution_model=model)
    assert configuration.execution_model is model
    assert configuration.model_copy().execution_model is model
    assert "model=" not in repr(configuration)
    assert "model=" not in str(configuration)

    class Container(BaseModel):
        configuration: nh.StepExecutorConfiguration

    container = Container(configuration=configuration)
    for record in (configuration, container):
        for dump in (record.model_dump, record.model_dump_json):
            with pytest.raises(PydanticSerializationError, match="exclude the live model field explicitly"):
                dump()
    assert "execution_model" not in configuration.model_dump(exclude={"execution_model"})
    configuration.model_dump_json(exclude={"execution_model"})
    assert configuration.model_dump(include={"tokenizer_encoding"}) == {"tokenizer_encoding": None}
    assert "execution_model" not in container.model_dump(exclude={"configuration": {"execution_model"}})["configuration"]
    container.model_dump_json(exclude={"configuration": {"execution_model"}})


@pytest.mark.parametrize("value", [1, {}, {"model_name": "gpt-6-luna"}, object()])
def test_only_existing_models_or_qualified_strings_are_accepted(value: object) -> None:
    with pytest.raises(ValidationError):
        nh.StepExecutorConfiguration(execution_model=value)  # type: ignore[arg-type]


def test_string_configuration_round_trip_and_schema() -> None:
    configuration = nh.StepExecutorConfiguration(execution_model="openai-responses:gpt-6-luna")
    assert nh.StepExecutorConfiguration.model_validate_json(configuration.model_dump_json()) == configuration
    assert nh.StepExecutorConfiguration.model_json_schema()["properties"]["execution_model"]["anyOf"] == [{"type": "string"}, {"type": "null"}]


@pytest.mark.parametrize(("name", "expected"), [("gpt-4", "cl100k_base"), ("unknown-model", "o200k_base")])
def test_tokenizer_model_names_and_explicit_override(name: str, expected: str) -> None:
    model = model_named(name)
    assert nh.StepExecutorConfiguration(execution_model=model).resolve_token_encoding().name == expected
    assert nh.StepExecutorConfiguration(execution_model=model, tokenizer_encoding="cl100k_base").resolve_token_encoding().name == "cl100k_base"
    with pytest.raises(ValueError):
        nh.StepExecutorConfiguration(execution_model=model, tokenizer_encoding="invalid-encoding").resolve_token_encoding()


def test_run_configuration_model_default_applies():
    configuration = nh.StepExecutorConfiguration()
    assert configuration.execution_model is None
    assert configuration.decision_model is None


def test_run_configuration_model_requires_provider_model_format():
    with pytest.raises(ValueError, match="provider:model"):
        nh.StepExecutorConfiguration(execution_model="openai-responses")

    with pytest.raises(ValueError, match="provider:model"):
        nh.StepExecutorConfiguration(execution_model=":gpt-6-luna")

    with pytest.raises(ValueError, match="provider:model"):
        nh.StepExecutorConfiguration(execution_model="openai-responses:")

    with pytest.raises(ValueError, match="provider:model"):
        nh.StepExecutorConfiguration(execution_model="openai-responses:gpt-6-luna:extra")
