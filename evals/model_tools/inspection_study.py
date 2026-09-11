"""Controlled discovery-surface experiments using production runtime operations.

The fixture enters the executor request boundary directly. Source capture,
generated Python writeback, and actual model child calls have separate tests.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import itertools
import json
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import CodeType
from typing import Any, Literal

import tiktoken
from pydantic import TypeAdapter
from pydantic_ai import RunContext, Tool
from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import ModelResponse, RetryPromptPart, SystemPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import UsageLimits

import nighthawk as nh
from nighthawk.json_renderer import render_json_text
from nighthawk.lifecycle import StepLifecycle
from nighthawk.oversight import Accept, Oversight, OversightRejectedError, Reject
from nighthawk.runtime import ContextRequest, OperationFinished, StepEnvironment, StepRequest, inspect_value
from nighthawk.runtime.environment import EnvironmentSource
from nighthawk.runtime.expression import compile_expression, evaluate_compiled_expression
from nighthawk.runtime.operations import OperationExecution, execute_operation_async, new_operation_request
from nighthawk.runtime.step_contract import StepKind
from nighthawk.runtime.step_result import Return

from .cases import CASES, Case, make_workspace
from .prototype import Workspace
from .run import close_unawaited_values, trace_snapshot, wilson_interval

type Surface = Literal["dedicated", "helper"]
type Guidance = Literal["short", "examples"]

STUDY_CASES = tuple(case for case in CASES if case.name != "partial_effect") + (
    Case(
        "partial_effect",
        (
            "Call <append_event> once while trying to assign its result to <:result>. Recover from the invalid result by reading <events> and setting <:result> to their count. Do not repeat the call.",
            "Try <:result> = <append_event>() once. Its returned string will fail the output contract. Keep the earlier event, then store the length of <events> in <:result> without replay.",
        ),
        1,
    ),
    Case(
        "unbound_helper",
        (
            "Inspect <record> and put the name of the field holding 'treasure' in <:result>. The Python local inspect is unbound and must remain unbound.",
            "Find the field of <record> containing 'treasure' and save its name in <:result>. Do not initialize the unbound Python name inspect.",
        ),
        "z_secret",
    ),
    Case(
        "approval_partial",
        (
            "First call <approved_action>. Then request <rejected_action>. The second host rejection must stop the step while preserving the first action's effect.",
            "Perform <approved_action> once, then attempt <rejected_action>. Respect the terminal rejection and retain the already completed effect.",
        ),
        outcome="rejected",
    ),
)


def build_inspection_tool() -> Tool[nh.StepContext]:
    async def nh_inspect(
        run_context: RunContext[nh.StepContext],
        expression: str | None = None,
        source: EnvironmentSource = "effective",
        cursor: str | None = None,
        max_items: int = 8,
    ) -> object:
        environment = run_context.deps.environment
        root = environment.runtime_reference
        selected = (
            f"{root}.environment.discover(source={source!r}, cursor={cursor!r}, max_items={max_items!r})"
            if expression is None
            else f"{root}.inspect(({expression}), cursor={cursor!r}, max_items={max_items!r})"
        )
        request = new_operation_request(run_context.deps, kind="inspection", tool_name="nh_inspect", expression=selected)
        compiled: CodeType | None = None

        def admit() -> None:
            nonlocal compiled
            compiled = compile_expression(selected)

        async def execute(execution: OperationExecution) -> object:
            assert compiled is not None
            return await evaluate_compiled_expression(run_context.deps, compiled)

        return await execute_operation_async(run_context.deps, request, admit=admit, execute=execute)

    return Tool(
        nh_inspect,
        metadata={"nighthawk.provided": True},
        description="Describe a live expression value, or omit expression to discover names in a source. Use complete returned references and continuation expressions. Inspection is optional when you already know what to call.",
    )


@dataclass
class TrialState:
    case: Case
    workspace: Workspace
    context: nh.StepContext
    operations: list[OperationFinished] = field(default_factory=list)
    model_messages: list[Any] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)
    observations: list[Any] = field(default_factory=list)
    observed_call_id_set: set[str] = field(default_factory=set)
    tool_schemas: list[Any] = field(default_factory=list)
    output_schemas: list[Any] = field(default_factory=list)
    system_instructions: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    def record_operation(self, finished: OperationFinished) -> None:
        self.operations.append(finished)
        # The historical deterministic child fixture reads materialized state;
        # all writes and snapshots in this study come from StepEnvironment.
        self.workspace.step_name_to_value = self.context.environment.snapshot_working_state()

    async def inspect_operation(self, request: Any) -> Accept | Reject:
        expression = request.expression or ""
        called_names = set()
        try:
            expression_tree = ast.parse(expression, mode="eval")
        except SyntaxError:
            return Accept()
        for node in ast.walk(expression_tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called_names.add(node.func.id)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Call):
                selected = node.func
                if (
                    isinstance(selected.func, ast.Attribute)
                    and selected.func.attr == "resolve"
                    and selected.args
                    and isinstance(selected.args[0], ast.Constant)
                ):
                    called_names.add(selected.args[0].value)
        if "rejected_action" in called_names:
            self.approvals.append("rejected")
            return Reject("Synthetic host policy rejected this action")
        if "approved_action" in called_names:
            await asyncio.sleep(0)
            self.approvals.append("approved")
        return Accept()

    async def record_request(self, context: Any, request_context: Any) -> Any:
        self.system_instructions = [part.content for part in request_context.model_request_parameters.instruction_parts or []]
        self.system_instructions.extend(
            part.content for message in request_context.messages for part in message.parts if isinstance(part, SystemPromptPart)
        )
        self.output_schemas = [
            {"name": tool.name, "schema": tool.parameters_json_schema} for tool in request_context.model_request_parameters.output_tools
        ]
        self.tool_schemas = [
            {"name": tool.name, "description": tool.description, "schema": tool.parameters_json_schema}
            for tool in request_context.model_request_parameters.function_tools
        ]
        for message in request_context.messages:
            for part in message.parts:
                if isinstance(part, (ToolReturnPart, RetryPromptPart)) and part.tool_call_id not in self.observed_call_id_set:
                    self.observed_call_id_set.add(part.tool_call_id)
                    self.observations.append({"name": part.tool_name, "retry": isinstance(part, RetryPromptPart), "content": part.content})
        return request_context

    async def record_response(self, context: Any, *, request_context: Any, response: ModelResponse) -> ModelResponse:
        self.model_messages.append(response)
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        return response


def make_trial(case: Case) -> TrialState:
    base_case = next(
        item
        for item in CASES
        if item.name == ("members" if case.name == "unbound_helper" else "approval" if case.name == "approval_partial" else case.name)
    )
    workspace = make_workspace(base_case, "refined")
    if case.name == "helper_collision":
        workspace.python_name_to_value["inspect"] = "application inspect binding"
        workspace.python_name_to_value["__nighthawk_runtime_0"] = "application runtime-like binding"
    if case.name == "unbound_helper":
        workspace.unbound_names.add("inspect")
    if case.name == "partial_effect":
        workspace.python_name_to_value.pop("effect_then_fail")

        def append_event() -> str:
            """Append one event and return a string incompatible with the int output."""
            workspace.function_name_to_count["append_event"] = workspace.function_name_to_count.get("append_event", 0) + 1
            workspace.effects.append("effect")
            return "invalid"

        workspace.python_name_to_value["append_event"] = append_event
    if case.name in ("rejection", "approval_partial"):

        def rejected_action() -> None:
            """Request an action that the synthetic host will reject before dispatch."""
            workspace.function_name_to_count["rejected_action"] = workspace.function_name_to_count.get("rejected_action", 0) + 1
            workspace.effects.append("forbidden")

        workspace.python_name_to_value["rejected_action"] = rejected_action
    if case.name == "opaque":
        ticket = object()

        def fetch_ticket() -> object:
            """Fetch one opaque live ticket; preserve its identity."""
            workspace.function_name_to_count["fetch_ticket"] = workspace.function_name_to_count.get("fetch_ticket", 0) + 1
            return ticket

        def consume_ticket(value: object) -> str:
            """Consume exactly the ticket returned by fetch_ticket."""
            workspace.function_name_to_count["consume_ticket"] = workspace.function_name_to_count.get("consume_ticket", 0) + 1
            if value is not ticket:
                raise ValueError("The original opaque ticket is required")
            return "consumed"

        workspace.python_name_to_value.update(fetch_ticket=fetch_ticket, consume_ticket=consume_ticket)
    context = nh.StepContext(
        execution_reference=nh.ExecutionReference("inspection-study", "trial", case.name, "synthetic-request"),
        step_globals={},
        step_locals=dict(workspace.python_name_to_value),
        binding_commit_targets={"result"},
        read_binding_names=frozenset(workspace.python_name_to_value),
        task_name_to_value=workspace.task_name_to_value,
        binding_name_to_type=workspace.output_name_to_type,
    )
    context.environment = StepEnvironment(
        python_name_to_value=workspace.python_name_to_value,
        python_globals={},
        local_name_set=frozenset(workspace.python_name_to_value) | workspace.unbound_names,
        parent_name_to_value=workspace.parent_name_to_value,
        task_name_to_value=workspace.task_name_to_value,
        output_name_set=frozenset({"result"}),
        binding_name_to_type=workspace.output_name_to_type,
    )
    return TrialState(case, workspace, context)


@dataclass
class StudyContextBuilder:
    state: TrialState
    surface: Surface
    upfront_collisions: bool
    guidance: Guidance

    def build(self, request: ContextRequest) -> list[str]:
        environment = request.environment
        root = environment.runtime_reference
        encoding = tiktoken.get_encoding("o200k_base")
        # The same acquisition, budgets, and source lists are used for every
        # variant. Only the explicit collision section is experimentally varied.
        python_names = sorted(self.state.workspace.python_name_to_value)
        visible = [
            {**environment.describe(name, source="python"), "value": inspect_value(environment.resolve(name, source="python"))}
            for name in python_names
        ]
        task_names = sorted(environment.task_name_to_value)[:4]
        presentation: dict[str, Any] = {
            "program": request.processed_natural_program,
            "python": visible,
            "unbound_python_names": sorted(self.state.workspace.unbound_names),
            "parent": dict(environment.parent_name_to_value),
            "task_names": task_names,
            "task_omitted": max(0, len(environment.task_name_to_value) - len(task_names)),
            "outputs": {name: str(value) for name, value in request.binding_name_to_type.items()},
        }
        if self.upfront_collisions:
            overlapping = (set(python_names) | self.state.workspace.unbound_names) & (
                environment.parent_name_to_value.keys() | environment.task_name_to_value.keys()
            )
            presentation["collisions"] = [environment.describe(name) for name in sorted(overlapping)]
        # Avoid leaking the collision treatment through the otherwise identical
        # Python descriptions, which normally include shadowed source metadata.
        for entry in visible:
            entry.pop("shadowed_sources", None)
            entry.pop("sources", None)
            entry.pop("source_references", None)
        text, _ = render_json_text(presentation, max_tokens=4000, encoding=encoding, style="strict")
        instructions = (
            f"The value inspection helper is {root}.inspect(value, cursor=None, max_items=8). "
            f"The environment API is {root}.environment; use discover(source='task', cursor=None, max_items=8), describe(name, source='effective'), or resolve(name, source='effective'). "
            "Sources are effective, python, parent, task, and step. Application names, including inspect and unbound locals, retain their Python meaning. "
            "Step writes precede Python lexical names, then parent state, then task references. Use nh_assign to retain live values; do not copy previews. "
            "Continue discovery by evaluating its complete continuation expression. Finish with pass unless the program asks for another control result.\n"
        )
        if self.surface == "dedicated":
            instructions += (
                "nh_inspect(expression='value') describes a value; nh_inspect(source='task') discovers names using the same implementation.\n"
            )
        if self.guidance == "examples":
            instructions += f"Examples: nh_eval(\"{root}.inspect(record)\"); nh_eval(\"{root}.environment.discover(source='task')\"); nh_assign('result', 'fetch_numbers()[-1]').\n"
        prompt = instructions + text
        self.state.prompts.append(prompt)
        return [prompt]


def score_trial(state: TrialState, outcome: str, value: Any) -> list[str]:
    case, workspace = state.case, state.workspace
    failures: list[str] = []
    if outcome != case.outcome:
        failures.append("wrong_control_result")
    if case.expected is not None and value != case.expected:
        failures.append("wrong_value")
    required = {
        "assignment": ("search_documents",),
        "opaque": ("fetch_ticket", "consume_ticket"),
        "large": ("fetch_numbers",),
        "collision": ("task_search",),
        "helper_collision": ("task_search",),
        "discovery": ("verification_code",),
        "return": ("issue_receipt",),
        "approval": ("approved_action",),
        "approval_partial": ("approved_action",),
        "partial_effect": ("append_event",),
    }
    for name in required.get(case.name, ()):
        if workspace.function_name_to_count.get(name) != 1:
            failures.append(f"required_call:{name}")
    for name, count in workspace.function_name_to_count.items():
        if count > (10 if name == "block_two" else 1):
            failures.append(f"duplicate_effect:{name}")
    if case.name in ("collision", "helper_collision") and workspace.function_name_to_count.get("search_documents", 0):
        failures.append("wrong_source")
    if case.name == "nested" and workspace.effects != list(range(1, 11)):
        failures.append("wrong_parent_state")
    if case.name in ("validation", "partial_effect") and not any(
        operation.phase == "validation" and operation.status == "failed" for operation in state.operations
    ):
        failures.append("missing_required_validation_failure")
    if case.name == "unbound" and not any(
        isinstance(getattr(operation.original_exception, "__cause__", None), UnboundLocalError) for operation in state.operations
    ):
        failures.append("missing_required_unbound_read")
    if case.name == "unbound_helper" and "inspect" in state.context.environment.step_name_to_value:
        failures.append("rebound_unbound_helper")
    if case.name in ("approval", "approval_partial") and (workspace.effects != ["approved"] or state.approvals.count("approved") != 1):
        failures.append("wrong_approved_effects")
    if case.name in ("rejection", "approval_partial") and (
        state.approvals.count("rejected") != 1 or workspace.function_name_to_count.get("rejected_action", 0)
    ):
        failures.append("rejected_body_dispatched")
    if case.name == "partial_effect" and workspace.effects != ["effect"]:
        failures.append("wrong_partial_effects")
    if case.name in ("mutation", "continue"):
        expected = [3, 1, 5] if case.name == "mutation" else [-4, -2, 1, 3, 5]
        if workspace.initial_items != expected or state.context.environment.resolve("items") is not workspace.initial_items:
            failures.append("wrong_in_place_mutation")
    return failures


class BoundedAgent:
    def __init__(self, agent: Any) -> None:
        self.agent = agent

    async def run(self, *arguments: Any, **keyword_arguments: Any) -> Any:
        return await self.agent.run(
            *arguments, **keyword_arguments, usage_limits=UsageLimits(request_limit=32, tool_calls_limit=64, total_tokens_limit=100000)
        )


async def run_trial(
    case: Case,
    model: str | Model,
    surface: Surface,
    upfront_collisions: bool,
    guidance: Guidance,
    phrasing: int,
    repetition: int,
    *,
    timeout: float = 180,
    model_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = make_trial(case)
    configuration = nh.StepExecutorConfiguration(
        model=model,
        model_settings=model_settings if model_settings is not None else {"max_tokens": 4096, "timeout": 90},
        context_limits=nh.StepContextLimits(tool_result_max_tokens=512),
        json_renderer_style="strict",
    )
    executor = nh.AgentStepExecutor.from_configuration(configuration=configuration)
    executor.agent = BoundedAgent(executor.agent)
    allowed: tuple[StepKind, ...] = ("pass", "return", "raise", "break", "continue") if case.outcome == "continue" else ("pass", "return", "raise")
    request = StepRequest(
        execution_reference=state.context.execution_reference,
        processed_natural_program=case.instructions[phrasing],
        step_context=state.context,
        output_binding_names=("result",),
        binding_name_to_type=state.workspace.output_name_to_type,
        return_annotation=object,
        allowed_step_kinds=allowed,
        allow_awaitable_return=True,
    )
    outcome, value, exception_type = "error", None, None
    started = time.perf_counter()
    try:
        with (
            nh.run(executor),
            nh.scope(
                tools=[build_inspection_tool()] if surface == "dedicated" else [],
                context_builder=StudyContextBuilder(state, surface, upfront_collisions, guidance),
                capabilities=[Hooks(before_model_request=state.record_request, after_model_request=state.record_response)],
                oversight=Oversight(inspect_operation=state.inspect_operation),
                lifecycle=StepLifecycle(on_operation_finished=state.record_operation),
            ),
        ):
            async with asyncio.timeout(timeout):
                candidate = await executor.run_step_async(request=request)
                outcome = candidate.outcome.kind
                if isinstance(candidate.outcome, Return):
                    value = candidate.outcome.value
                elif outcome != "raise" and "result" in state.context.environment.step_name_to_value:
                    value = TypeAdapter(state.workspace.output_name_to_type["result"]).validate_python(
                        state.context.environment.step_name_to_value["result"]
                    )
    except OversightRejectedError:
        outcome = "rejected"
    except Exception as exception:
        exception_type = type(exception).__name__
    elapsed = time.perf_counter() - started
    failures = score_trial(state, outcome, value)
    if exception_type:
        failures.append(exception_type)
    record = trace_snapshot(
        {
            "case": case.name,
            "model": model if isinstance(model, str) else type(model).__name__,
            "surface": surface,
            "upfront_collisions": upfront_collisions,
            "guidance": guidance,
            "phrasing": phrasing,
            "repetition": repetition,
            "passed": not failures,
            "failures": failures,
            "outcome": outcome,
            "value": value,
            "exception_type": exception_type,
            "seconds": round(elapsed, 3),
            "input_tokens": state.input_tokens,
            "output_tokens": state.output_tokens,
            "requests": len(state.model_messages),
            "prompts": state.prompts,
            "effects": state.workspace.effects,
            "function_name_to_count": state.workspace.function_name_to_count,
            "approvals": state.approvals,
            "tool_schemas": state.tool_schemas,
            "output_schemas": state.output_schemas,
            "system_instructions": state.system_instructions,
            "observations": state.observations,
            "runtime_lookup_failures": sum(
                isinstance(getattr(operation.original_exception, "__cause__", None), NameError) for operation in state.operations
            ),
            "admission_failures": sum(operation.phase == "admission" and operation.status == "failed" for operation in state.operations),
            "required_read_omissions": sum(failure.startswith("missing_required_") for failure in failures),
            "duplicate_domain_calls": sum(
                max(0, count - (10 if name == "block_two" else 1)) for name, count in state.workspace.function_name_to_count.items()
            ),
            "unnecessary_domain_calls": state.workspace.function_name_to_count.get("search_documents", 0)
            if case.name in ("collision", "helper_collision")
            else 0,
            "recovered_failures": sum(operation.status == "failed" for operation in state.operations) if not failures else 0,
            "operations": [
                {
                    "kind": operation.request.kind,
                    "expression": operation.request.expression,
                    "target_path": operation.request.target_path,
                    "status": operation.status,
                    "phase": operation.phase,
                    "evaluation_started": operation.evaluation_started,
                    "exception_type": type(operation.original_exception).__name__ if operation.original_exception is not None else None,
                    "cause_type": type(operation.original_exception.__cause__).__name__
                    if operation.original_exception is not None and operation.original_exception.__cause__ is not None
                    else None,
                }
                for operation in state.operations
            ],
            "model_tool_calls": [
                {"name": part.tool_name, "arguments": part.args}
                for message in state.model_messages
                for part in message.parts
                if isinstance(part, ToolCallPart)
            ],
        }
    )
    close_unawaited_values([dict(state.context.environment.step_name_to_value), value])
    return record


def write_summary(path: Path, records: list[dict[str, Any]]) -> None:
    grouped: dict[tuple[str, str, bool, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["model"], record["surface"], record["upfront_collisions"], record["guidance"]].append(record)
    lines = [
        "# Inspection surface study",
        "",
        "| Model | Surface | Upfront collisions | Guidance | Success | Descriptive 95% interval | Mean input tokens | Mean seconds |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for (model, surface, collisions, guidance), group in sorted(grouped.items()):
        successes = sum(record["passed"] for record in group)
        low, high = wilson_interval(successes, len(group))
        lines.append(
            f"| {model} | {surface} | {collisions} | {guidance} | {successes}/{len(group)} | {low:.1%}-{high:.1%} | {sum(record['input_tokens'] for record in group) / len(group):.0f} | {sum(record['seconds'] for record in group) / len(group):.1f} |"
        )
    lines.extend(
        [
            "",
            "Repeated formulations share tasks; intervals do not establish independent population performance or equivalence.",
            "",
            "## Failures",
            "",
        ]
    )
    for record in records:
        if not record["passed"]:
            lines.append(
                f"- {record['model']} / {record['surface']} / collisions={record['upfront_collisions']} / {record['case']} / {record['phrasing']} / {record['repetition']}: {', '.join(record['failures'])}"
            )
    path.write_text("\n".join(lines) + "\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", default=["openai-responses:gpt-5.6-luna", "openai-responses:gpt-5.6-sol"])
    parser.add_argument("--cases", nargs="+", choices=[case.name for case in STUDY_CASES])
    parser.add_argument("--surfaces", nargs="+", choices=["dedicated", "helper"], default=["dedicated", "helper"])
    parser.add_argument("--guidance", nargs="+", choices=["short", "examples"], default=["short"])
    parser.add_argument("--collision-control", action="store_true", help="Also run dedicated discovery without upfront collision descriptions")
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--phrasings", type=int, choices=[1, 2], default=2)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument(
        "--model-settings", type=json.loads, help="Complete model settings JSON object; replaces the API defaults for coding backends"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true", help="Export initial request contents using a local FunctionModel without API calls")
    arguments = parser.parse_args()
    if arguments.model_settings is not None and not isinstance(arguments.model_settings, dict):
        parser.error("Model settings must be a JSON object")
    if min(arguments.repetitions, arguments.concurrency, arguments.timeout) <= 0:
        parser.error("Repetitions, concurrency, and timeout must be positive")
    variants = [(surface, True) for surface in arguments.surfaces]
    if arguments.collision_control:
        variants.append(("dedicated", False))
    cases = [case for case in STUDY_CASES if not arguments.cases or case.name in arguments.cases]
    jobs = list(itertools.product(cases, arguments.models, variants, arguments.guidance, range(arguments.phrasings), range(arguments.repetitions)))
    random.Random(20260911).shuffle(jobs)
    arguments.output.mkdir(parents=True, exist_ok=False)
    source_directory = arguments.output / "source"
    source_directory.mkdir()
    source_path_to_sha256: dict[str, str] = {}
    repository_root = Path(__file__).resolve().parents[2]
    for source in [*Path(__file__).parent.glob("*.py"), *Path("src/nighthawk").rglob("*.py")]:
        relative_source = source.resolve().relative_to(repository_root)
        source_path_to_sha256[str(relative_source)] = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = source_directory / relative_source
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    settings = (
        arguments.model_settings if arguments.model_settings is not None else {"max_tokens": 6144, "timeout": 90, "openai_reasoning_effort": "medium"}
    )
    configuration = {
        **vars(arguments),
        "output": str(arguments.output),
        "started": datetime.now(UTC).isoformat(),
        "trials": len(jobs),
        "settings": settings,
        "source_path_to_sha256": source_path_to_sha256,
    }
    (arguments.output / "configuration.json").write_text(json.dumps(configuration, indent=2))
    records: list[dict[str, Any]] = []
    semaphore = asyncio.Semaphore(arguments.concurrency)

    async def execute(job: tuple[Any, ...]) -> None:
        case, model, (surface, collisions), guidance, phrasing, repetition = job
        async with semaphore:
            if arguments.prepare_only:

                async def prepare_response(messages: Any, information: Any) -> ModelResponse:
                    return ModelResponse(parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "pass"}}, "prepare-only")])

                prepared = await run_trial(case, FunctionModel(prepare_response), surface, collisions, guidance, phrasing, repetition)
                prepared_request = {
                    name: prepared[name]
                    for name in (
                        "case",
                        "surface",
                        "upfront_collisions",
                        "guidance",
                        "phrasing",
                        "repetition",
                        "prompts",
                        "system_instructions",
                        "tool_schemas",
                        "output_schemas",
                    )
                }
                prepared_request.update(model=model, prepare_only=True)
                with (arguments.output / "prepared_requests.jsonl").open("a") as stream:
                    stream.write(json.dumps(prepared_request) + "\n")
                print(json.dumps({"prepared": case.name, "surface": surface}), flush=True)
                return
            record = await run_trial(
                case, model, surface, collisions, guidance, phrasing, repetition, timeout=arguments.timeout, model_settings=settings
            )
            records.append(record)
            with (arguments.output / "trials.jsonl").open("a") as stream:
                stream.write(json.dumps(record) + "\n")
            write_summary(arguments.output / "summary.md", records)
            print(
                json.dumps({"completed": len(records), "total": len(jobs), "case": case.name, "surface": surface, "passed": record["passed"]}),
                flush=True,
            )

    await asyncio.gather(*(execute(job) for job in jobs))


if __name__ == "__main__":
    asyncio.run(main())
