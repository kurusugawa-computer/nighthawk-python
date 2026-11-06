"""Live single-model acceptance through the public foreground Session API."""

from __future__ import annotations

import os

import pytest

from nighthawk.agent import CapabilityInvocationEvent, DispatchEvent, NaturalExecutionOutcomeEvent, RunEndEvent, RunEntryEvent, RunLedger
from nighthawk.agent.session import CellInput, create_session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("OPENAI_API_KEY" not in os.environ, reason="OPENAI_API_KEY is required for live Session acceptance"),
]


def assert_one_generated_entry(ledger: RunLedger, argument: int) -> None:
    dispatches = [entry.event for entry in ledger.select(DispatchEvent) if isinstance(entry.event, DispatchEvent)]
    materializations = [event for event in dispatches if event.dispatch_kind == "definition_only"]
    assert len(materializations) == 1
    entries = [event for event in dispatches if event.dispatch_kind == "invocation" and event.parent_node_id == materializations[0].parent_node_id]
    # The requested call enters once; recursive descendants remain ordinary composition.
    assert len(entries) == 1
    assert entries[0].argument_snapshot == {"positional": (argument,), "keyword": {}}


async def test_generated_function_declaration_python_call_and_natural_reuse() -> None:
    from nighthawk.agent.session import SessionFunction

    session = create_session(execution_model="openai-responses:gpt-6-luna")
    try:
        outcome = await session.submit(
            CellInput("Declare a function <:fib> computing the zero-indexed Fibonacci number. Do not call it yet.")
        ).wait_async()
        print("\n".join(output.text for output in outcome.output_tuple))
        assert outcome.status == "success", outcome.output_tuple
        function = session.binding_name_to_value["fib"]
        assert isinstance(function, SessionFunction)
        assert function.is_async is False
        declaration_ledger = session._run_id_to_ledger[outcome.run_id_tuple[0]]
        print(
            "Declaration dispatches:",
            [
                (entry.event.dispatch_kind, entry.event.export_name_tuple)
                for entry in declaration_ledger.select(DispatchEvent)
                if isinstance(entry.event, DispatchEvent)
            ],
        )
        assert len(declaration_ledger.select(DispatchEvent)) == 1

        outcome = await session.submit(CellInput("answer = fib(3)", mode="python")).wait_async()
        assert outcome.status == "success", outcome.output_tuple
        assert session.binding_name_to_value["answer"] == 2
        invocation_ledger = session._run_id_to_ledger[outcome.run_id_tuple[0]]
        assert_one_generated_entry(invocation_ledger, 3)
        assert not invocation_ledger.select(NaturalExecutionOutcomeEvent)

        outcome = await session.submit(CellInput("Call fib with 10 once and assign its result to <:answer>.")).wait_async()
        print("\n".join(output.text for output in outcome.output_tuple))
        assert outcome.status == "success", outcome.output_tuple
        assert session.binding_name_to_value["answer"] == 55
        assert len(outcome.run_id_tuple) == 1
        assert_one_generated_entry(session._run_id_to_ledger[outcome.run_id_tuple[0]], 10)

        outcome = await session.submit(
            CellInput("Declare an asynchronous function <:increment> returning its integer argument plus one. Do not call it yet.")
        ).wait_async()
        print("\n".join(output.text for output in outcome.output_tuple))
        assert outcome.status == "success", outcome.output_tuple
        assert isinstance(session.binding_name_to_value["increment"], SessionFunction)
        assert session.binding_name_to_value["increment"].is_async
        outcome = await session.submit(CellInput("answer = await increment(3)", mode="python")).wait_async()
        assert outcome.status == "success", outcome.output_tuple
        assert session.binding_name_to_value["answer"] == 4
        outcome = await session.submit(CellInput("Call <increment> with 9 once and assign its result to <:answer>.")).wait_async()
        print("\n".join(output.text for output in outcome.output_tuple))
        assert outcome.status == "success", outcome.output_tuple
        assert session.binding_name_to_value["answer"] == 10
    finally:
        assert session.close()


async def test_configured_confinement_refusal_does_not_publish_inert_source() -> None:
    from dataclasses import replace

    from nighthawk.agent.runtime import unconfirmed_perimeter_confinement

    session = create_session(execution_model="openai-responses:gpt-6-luna")
    session._runner = replace(session._runner, perimeter_confinement=unconfirmed_perimeter_confinement())
    try:
        outcome = await session.submit(
            CellInput("Declare a callable function <:fib> computing the zero-indexed Fibonacci number. Do not call it yet.")
        ).wait_async()
        print("\n".join(output.text for output in outcome.output_tuple))
        assert outcome.status == "failure", (outcome.output_tuple, session.binding_name_to_value.get("fib"))
        assert "fib" not in session.binding_name_to_value
        assert not any(output.kind == "publication" for output in outcome.output_tuple)
        assert any("confinement_unavailable" in output.text for output in outcome.output_tuple)
    finally:
        assert session.close()


async def test_single_model_conversation_calculation_retention_and_function_call() -> None:
    session = create_session(execution_model="openai-responses:gpt-6-luna")
    try:
        assert (await session.submit(CellInput("> samples = [1, 2, 3]\n_function_call_list = []")).wait_async()).status == "success"
        tasks = [
            "Explain samples and assign its sum to <:total>.",
            "Call add_one with total and assign the result to <:incremented>.",
        ]
        for i, task in enumerate(tasks):
            if i == 1:
                definition = CellInput("> def add_one(value: int) -> int:\n    _function_call_list.append(('add_one', value))\n    return value + 1")
                assert (await session.submit(definition).wait_async()).status == "success"
            execution = session.submit(CellInput(task))
            outcome = await execution.wait_async()
            for run_id in execution.run_id_tuple:
                ledger = session._run_id_to_ledger[run_id]
                entry = ledger.select(RunEntryEvent)[0].event
                assert isinstance(entry, RunEntryEvent)
                observations = [record.event for record in ledger.select(NaturalExecutionOutcomeEvent)]
                requests = sum(event.executor_usage.request_count for event in observations if isinstance(event, NaturalExecutionOutcomeEvent))
                tokens = sum(event.executor_usage.total_token_count for event in observations if isinstance(event, NaturalExecutionOutcomeEvent))
                models = [
                    name for event in observations if isinstance(event, NaturalExecutionOutcomeEvent) for name in event.response_model_name_tuple
                ]
                print(
                    f"Session live run={run_id} mode={entry.executor_step_observation} models={models} requests={requests} tokens={tokens} status={outcome.status}"
                )
                assert requests <= 24
                assert not ledger.select(DispatchEvent)
            print("\n".join(output.text for output in outcome.output_tuple if output.kind in ("answer", "failure", "error")))
            assert outcome.status == "success", outcome.output_tuple
            assert session.binding_name_to_value["total" if i == 0 else "incremented"] == (6 if i == 0 else 7)
            assert session.binding_name_to_value["_function_call_list"] == ([] if i == 0 else [("add_one", 6)])
        definition_source = '''@nh.function
async def describe(value: int) -> str:
    _function_call_list.append(("describe", value))
    result: str = ""
    """execute
    ---
    deny: [raise, return, break, continue]
    ---
    Describe <value> in one short sentence and write the description to <:result>.
    """
    return result
'''
        assert (await session.submit(CellInput(definition_source, mode="python")).wait_async()).status == "success"
        for cell in (CellInput("> description = await describe(total)"), CellInput("Call describe with total and show its description.")):
            call_count = len(session.binding_name_to_value["_function_call_list"])
            execution = session.submit(cell)
            outcome = await execution.wait_async()
            print(f"Natural live status={outcome.status} runs={outcome.run_id_tuple}")
            for run_id in outcome.run_id_tuple:
                ledger = session._run_id_to_ledger[run_id]
                observations = [record.event for record in ledger.select(NaturalExecutionOutcomeEvent)]
                requests = sum(event.executor_usage.request_count for event in observations if isinstance(event, NaturalExecutionOutcomeEvent))
                tokens = sum(event.executor_usage.total_token_count for event in observations if isinstance(event, NaturalExecutionOutcomeEvent))
                print(f"Natural live run={run_id} requests={requests} tokens={tokens}")
                assert requests <= 24 and not ledger.select(DispatchEvent)
            print("\n".join(output.text for output in outcome.output_tuple if output.kind in ("answer", "result", "error", "failure")))
            assert outcome.status == "success", outcome.output_tuple
            assert len(outcome.run_id_tuple) == 1
            assert session.binding_name_to_value["_function_call_list"][call_count:] == [("describe", 6)]
        assert isinstance(session.binding_name_to_value["description"], str) and session.binding_name_to_value["description"]
    finally:
        assert session.close()


async def test_single_model_uses_older_history_without_implicit_retention() -> None:
    session = create_session(execution_model="openai-responses:gpt-6-luna")
    try:
        for i in range(105):
            assert (await session.submit(CellInput(f"> # old observation {i}")).wait_async()).status == "success"
        assert (await session.submit(CellInput("> # orchard historical value: 23")).wait_async()).status == "success"
        for i in range(25):
            assert (await session.submit(CellInput(f"> # recent observation {i}")).wait_async()).status == "success"
        prior_names = set(session.binding_name_to_value)
        execution = session.submit(CellInput("Search older conversation history for orchard and report the historical value recorded there."))
        outcome = await execution.wait_async()
        assert len(execution.run_id_tuple) == 1
        ledger = session._run_id_to_ledger[execution.run_id_tuple[0]]
        observations = [record.event for record in ledger.select(NaturalExecutionOutcomeEvent)]
        requests = sum(event.executor_usage.request_count for event in observations if isinstance(event, NaturalExecutionOutcomeEvent))
        tokens = sum(event.executor_usage.total_token_count for event in observations if isinstance(event, NaturalExecutionOutcomeEvent))
        models = [name for event in observations if isinstance(event, NaturalExecutionOutcomeEvent) for name in event.response_model_name_tuple]
        print(f"History live models={models} requests={requests} tokens={tokens} status={outcome.status}")
        print("\n".join(output.text for output in outcome.output_tuple if output.kind in ("answer", "result", "error", "failure")))
        assert outcome.status == "success", outcome.output_tuple
        assert any(
            isinstance(entry.event, CapabilityInvocationEvent) and entry.event.capability_name == "query_session_history"
            for entry in ledger.select(CapabilityInvocationEvent)
        )
        assert any("23" in output.text for output in outcome.output_tuple if output.kind in ("answer", "result"))
        assert set(session.binding_name_to_value) == prior_names
        assert requests <= 24 and not ledger.select(DispatchEvent)
    finally:
        assert session.close()


async def test_two_configured_models_direct_and_conversational_sync_natural_calls() -> None:
    import nighthawk as nh
    from nighthawk.agent import PerimeterPolicy
    from nighthawk.agent.runtime import Runner, bounded_resource_allocation
    from nighthawk.agent.session import SessionManager

    # Confirmed through the configured provider's models endpoint before acceptance.
    model_names = ("openai-responses:gpt-6-luna", "openai-responses:gpt-6.1-sol")
    runner = Runner.from_models(
        model_configuration_tuple=tuple(
            nh.StepExecutorConfiguration(execution_model=name, execution_model_settings={"max_tokens": 4000}) for name in model_names
        ),
        default_execution_model=model_names[0],
        perimeter_policy=PerimeterPolicy.NONE,
        resource_allocation_factory=lambda identity: bounded_resource_allocation(
            identity=identity.run_id,
            maximum_executor_step_count=24,
            maximum_total_token_count=60000,
        ),
    )
    session = SessionManager(runner=runner).create_session()
    source = '''_function_call_list = []

@nh.function
def probe() -> int:
    _function_call_list.append("probe")
    result: int = 0
    """execute
    ---
    deny: [return, raise, break, continue]
    ---
    Set <:result> to 7, then finish.
    """
    return result

@nh.function
def child() -> int:
    _function_call_list.append("child")
    with nh.scope(execution_model="openai-responses:gpt-6.1-sol"):
        return probe()

@nh.function
def parent() -> tuple[int, int, int]:
    _function_call_list.append("parent")
    first = probe()
    second = child()
    third = probe()
    return first, second, third
'''
    try:
        assert (await session.submit(CellInput(source, mode="python")).wait_async()).status == "success"
        for cell in (
            CellInput("> direct = parent()"),
            CellInput("Call parent() exactly once. Assign its returned tuple to <:conversational>. Do not call probe or child separately."),
        ):
            call_count = len(session.binding_name_to_value["_function_call_list"])
            outcome = await session.submit(cell).wait_async()
            assert outcome.status == "success", outcome.output_tuple
            assert len(outcome.run_id_tuple) == 1
            ledger = session._run_id_to_ledger[outcome.run_id_tuple[0]]
            events = [entry.event for entry in ledger.select(NaturalExecutionOutcomeEvent) if isinstance(entry.event, NaturalExecutionOutcomeEvent)]
            models = [name for event in events for name in event.response_model_name_tuple]
            requests = sum(event.executor_usage.request_count for event in events)
            tokens = sum(event.executor_usage.total_token_count for event in events)
            print(f"Two-model live run={outcome.run_id_tuple[0]} observation=executor_reports models={models} requests={requests} tokens={tokens}")
            print(f"Two-model accepted result={ledger.select(RunEndEvent)[0].event}")
            print("\n".join(output.text for output in outcome.output_tuple if output.kind in ("answer", "failure", "publication")))
            assert any(name is not None and "luna" in name for name in models)
            assert any(name is not None and "sol" in name for name in models)
            assert 0 < requests <= 24 and tokens > 0
            assert not ledger.select(DispatchEvent)
            assert session.binding_name_to_value["_function_call_list"][call_count:] == ["parent", "probe", "child", "probe", "probe"]
            if cell.text.startswith(">"):
                compressed = [name for i, name in enumerate(models) if i == 0 or name != models[i - 1]]
                assert len(compressed) == 3 and compressed[0] == compressed[2] and compressed[0] != compressed[1]
        assert session.binding_name_to_value["direct"] == (7, 7, 7)
        assert "conversational" in session.binding_name_to_value, outcome.output_tuple
        assert tuple(session.binding_name_to_value["conversational"]) == (7, 7, 7)
    finally:
        session.close()


async def test_live_lambda_private_read_and_explicit_write_bindings() -> None:
    session = create_session(execution_model="openai-responses:gpt-6-luna")
    try:
        source = "_calls = []\nf = lambda a: (_calls.append(a), a + 10)[1]\n_hidden: int = 7\ncombined: int = 0"
        assert (await session.submit(CellInput(source, mode="python")).wait_async()).status == "success"
        for task in (
            "Call f exactly once with 10 and assign its actual return value to <:answer>.",
            "Add <answer> and <_hidden>, assign the sum to <:combined>, and report it. Do not call f.",
            "Save 999 as combined. Do not call any function.",
        ):
            outcome = await session.submit(CellInput(task)).wait_async()
            print("\n".join(output.text for output in outcome.output_tuple))
            assert outcome.status == "success", outcome.output_tuple
        assert session.binding_name_to_value["answer"] == 20
        assert session.binding_name_to_value["combined"] == 27
        assert session.binding_name_to_value["_calls"] == [10]
        assert "_hidden" not in session._selection()[0]
    finally:
        session.close()
