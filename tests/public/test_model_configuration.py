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
    configuration = nh.StepExecutorConfiguration(model=model)
    assert configuration.model is model
    assert configuration.model_copy().model is model
    assert "model=" not in repr(configuration)
    assert "model=" not in str(configuration)

    class Container(BaseModel):
        configuration: nh.StepExecutorConfiguration

    container = Container(configuration=configuration)
    for record in (configuration, container):
        for dump in (record.model_dump, record.model_dump_json):
            with pytest.raises(PydanticSerializationError, match="exclude the model field explicitly"):
                dump()
    assert "model" not in configuration.model_dump(exclude={"model"})
    configuration.model_dump_json(exclude={"model"})
    assert configuration.model_dump(include={"tokenizer_encoding"}) == {"tokenizer_encoding": None}
    assert "model" not in container.model_dump(exclude={"configuration": {"model"}})["configuration"]
    container.model_dump_json(exclude={"configuration": {"model"}})


@pytest.mark.parametrize("value", [None, 1, {}, {"model_name": "gpt-4o"}, object()])
def test_only_existing_models_or_qualified_strings_are_accepted(value: object) -> None:
    with pytest.raises(ValidationError):
        nh.StepExecutorConfiguration(model=value)  # type: ignore[arg-type]


def test_string_configuration_round_trip_and_schema() -> None:
    configuration = nh.StepExecutorConfiguration(model="openai:gpt-4o")
    assert nh.StepExecutorConfiguration.model_validate_json(configuration.model_dump_json()) == configuration
    assert nh.StepExecutorConfiguration.model_json_schema()["properties"]["model"]["type"] == "string"


@pytest.mark.parametrize(("name", "expected"), [("gpt-4", "cl100k_base"), ("unknown-model", "o200k_base")])
def test_tokenizer_model_names_and_explicit_override(name: str, expected: str) -> None:
    model = model_named(name)
    assert nh.StepExecutorConfiguration(model=model).resolve_token_encoding().name == expected
    assert nh.StepExecutorConfiguration(model=model, tokenizer_encoding="cl100k_base").resolve_token_encoding().name == "cl100k_base"
    with pytest.raises(ValueError):
        nh.StepExecutorConfiguration(model=model, tokenizer_encoding="invalid-encoding").resolve_token_encoding()


def test_external_agent_rejects_instance_configuration() -> None:
    configuration = nh.StepExecutorConfiguration(model=model_named("local"))
    for construct in (nh.AgentStepExecutor, nh.AgentStepExecutor.from_agent):
        with pytest.raises(nh.NighthawkError, match="external agent owns model selection"):
            construct(configuration=configuration, agent=object())  # type: ignore[arg-type]
