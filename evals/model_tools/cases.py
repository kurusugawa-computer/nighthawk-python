"""Synthetic tasks with outcome/effect oracles independent of tool sequences."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from .prototype import ApprovalRejectedError, Surface, Workspace


@dataclass(frozen=True)
class Case:
    name: str
    instructions: tuple[str, str]
    expected: Any = None
    outcome: str = "pass"


CASES = (
    Case(
        "assignment",
        (
            "Call <search_documents> with <question> and store its result in <:result>.",
            "Set <:result> to the answer from <search_documents>(<question>).",
        ),
        "ANSWER",
    ),
    Case(
        "opaque",
        (
            "Fetch a ticket using <fetch_ticket>, retain it, and set <:result> to <consume_ticket> called with that exact ticket. Fetch once.",
            "Obtain one ticket from <fetch_ticket>. Give that same object to <consume_ticket> and save the returned text in <:result>.",
        ),
        "consumed",
    ),
    Case(
        "large",
        (
            "Retain the list from <fetch_numbers> and set <:result> to the sum of its last 100 values. Fetch once.",
            "Fetch numbers once with <fetch_numbers>. Keep the full list and put the sum of the final 100 entries in <:result>.",
        ),
        sum(range(9900, 10000)),
    ),
    Case(
        "mutation",
        (
            "Remove negative entries from <items> in place, preserving the existing list. Set <:result> to its length.",
            "Keep the nonnegative values in the same <items> list object. Save its resulting length to <:result>.",
        ),
        3,
    ),
    Case(
        "collision",
        (
            "Use the task-injected search_documents, not the Python function with that name, on Python-local <question>. Store the returned text in <:result>.",
            "Find search_documents in the task environment and call it with <question> from Python locals. Save its answer in <:result>.",
        ),
        "task:question",
    ),
    Case(
        "helper_collision",
        (
            "Use the task-injected search_documents with Python-local <question> and save its answer in <:result>. The local nh_environment must remain unchanged.",
            "Call the injected task search_documents on <question>. Store its text in <:result> without rebinding the existing nh_environment.",
        ),
        "task:question",
    ),
    Case(
        "unbound",
        (
            "Attempt to evaluate Python-local i. If it is unbound, put 'unbound' in <:result>; do not substitute the parent's i.",
            "Read i from the effective environment. Record 'unbound' in <:result> if Python reports an unbound local, without falling back to inherited i.",
        ),
        "unbound",
    ),
    Case(
        "nested",
        (
            "Call <block_two> ten times sequentially. Before each call set temporary i to the current one-based iteration 1 through 10. Collect each returned string in <:result>.",
            "For iterations 1 through 10, retain the counter as temporary i and then call <block_two>. Store the ten returned labels in <:result> in order.",
        ),
        ["ordinary"] * 4 + ["fifth", "ordinary", "ordinary", "eighth", "ordinary", "ordinary"],
    ),
    Case(
        "discovery",
        (
            "Find the task-injected callable whose description says it provides the verification code. Call it and save the code in <:result>.",
            "An injected task function provides the verification code. Discover it, invoke it, and put its returned code in <:result>.",
        ),
        "verified",
    ),
    Case(
        "members",
        (
            "Find the public field on <record> whose value is 'treasure'. Put that field's name in <:result>.",
            "Inspect <record> and find which public field holds 'treasure'. Save the field name, not its value, in <:result>.",
        ),
        "z_secret",
    ),
    Case(
        "validation",
        (
            "First try to assign the Python string 'invalid' to <:result>. After the validation error, assign the Python string '12' instead.",
            "Try storing 'invalid' in <:result>; then recover from its type error by storing '12'.",
        ),
        12,
    ),
    Case(
        "continue",
        (
            "Sort <items> in place and continue the enclosing Python loop.",
            "Arrange <items> in ascending order, then move to the next iteration of the enclosing loop.",
        ),
        outcome="continue",
    ),
    Case(
        "return",
        (
            "Return the result of <issue_receipt>() from this function. Issue exactly one receipt.",
            "Call <issue_receipt> once and return that receipt string from the function.",
        ),
        "receipt-1",
        "return",
    ),
    Case(
        "approval",
        (
            "Call <approved_action> and put its result in <:result>.",
            "Run <approved_action>, wait for it to finish, and save its answer in <:result>.",
        ),
        "approved",
    ),
    Case(
        "rejection",
        ("Attempt <rejected_action>. A host rejection must stop execution.", "Request <rejected_action> and obey the host's terminal rejection."),
        outcome="rejected",
    ),
    Case(
        "partial_effect",
        (
            "Call <effect_then_fail> once, retaining any return value in a temporary. If it fails, read <events> and set <:result> to their count. Do not repeat the call.",
            "Attempt <effect_then_fail> once. On failure inspect the existing <events>, save their length in <:result>, and finish without replaying the operation.",
        ),
        1,
    ),
)


def make_workspace(case: Case, surface: Surface) -> Workspace:
    workspace = Workspace(
        surface=surface,
        output_name_to_type={
            "result": int if case.name in ("large", "mutation", "validation", "partial_effect") else list[str] if case.name == "nested" else str
        },
    )

    def count(name: str) -> None:
        workspace.function_name_to_count[name] = workspace.function_name_to_count.get(name, 0) + 1

    def search_documents(question: str) -> str:
        """Return an answer to the question."""
        count("search_documents")
        return "ANSWER"

    ticket = SimpleNamespace(number=42)

    def fetch_ticket() -> object:
        """Return an opaque live ticket. Preserve this exact object."""
        count("fetch_ticket")
        return ticket

    def consume_ticket(value: object) -> str:
        """Consume the exact ticket object returned by fetch_ticket."""
        count("consume_ticket")
        if value is not ticket:
            raise ValueError("Expected the original live ticket")
        return "consumed"

    def fetch_numbers() -> list[int]:
        """Return 10000 numbers. The tool preview will be truncated."""
        count("fetch_numbers")
        return list(range(10000))

    def task_search(question: str) -> str:
        """Return the task answer for a question."""
        count("task_search")
        return "task:" + question

    async def block_two() -> str:
        """Read the parent's retained i; return fifth, eighth, or ordinary."""
        count("block_two")
        # A deterministic child fixture tests parent-call mechanics. It is not
        # a second LLM or a production Natural-function source transformation.
        inherited = {**workspace.parent_name_to_value, **workspace.python_name_to_value, **workspace.step_name_to_value}
        iteration = inherited.get("i")
        workspace.effects.append(iteration)
        await asyncio.sleep(0)
        return "fifth" if iteration == 5 else "eighth" if iteration == 8 else "ordinary"

    def verification_code() -> str:
        """Provide the verification code."""
        count("verification_code")
        return "verified"

    def issue_receipt() -> str:
        """Issue a new receipt. Calling twice issues two separate receipts."""
        count("issue_receipt")
        return f"receipt-{workspace.function_name_to_count['issue_receipt']}"

    async def approved_action() -> str:
        """Wait for host approval, then execute once."""
        count("approved_action")
        await asyncio.sleep(0)
        workspace.effects.append("approved")
        return "approved"

    async def rejected_action() -> None:
        """Ask the host; its rejection terminates this step before any effect."""
        count("rejected_action")
        await asyncio.sleep(0)
        raise ApprovalRejectedError("Host rejected the requested action")

    def effect_then_fail() -> int:
        """Append an event and then fail. Repeating this call duplicates the event."""
        count("effect_then_fail")
        workspace.effects.append("effect")
        raise ValueError("An event was appended, but the operation failed afterward")

    match case.name:
        case "assignment":
            workspace.python_name_to_value.update(search_documents=search_documents, question="question")
        case "opaque":
            workspace.python_name_to_value.update(fetch_ticket=fetch_ticket, consume_ticket=consume_ticket)
        case "large":
            workspace.python_name_to_value.update(fetch_numbers=fetch_numbers)
        case "mutation" | "continue":
            workspace.initial_items = [3, -2, 1, -4, 5]
            workspace.python_name_to_value["items"] = workspace.initial_items
        case "collision" | "helper_collision":
            workspace.python_name_to_value.update(search_documents=search_documents, question="question")
            workspace.task_name_to_value["search_documents"] = task_search
            if case.name == "helper_collision":
                workspace.python_name_to_value["nh_environment"] = "existing Python value"
        case "unbound":
            workspace.unbound_names.add("i")
            workspace.parent_name_to_value["i"] = 5
        case "nested":
            workspace.python_name_to_value["block_two"] = block_two
        case "discovery":
            workspace.task_name_to_value.update({f"item_{i}": i for i in range(9)})
            workspace.task_name_to_value["z_verify"] = verification_code
        case "members":
            workspace.python_name_to_value["record"] = SimpleNamespace(**{f"field_{i}": "ordinary" for i in range(9)}, z_secret="treasure")
        case "return":
            workspace.python_name_to_value["issue_receipt"] = issue_receipt
        case "approval":
            workspace.python_name_to_value["approved_action"] = approved_action
        case "rejection":
            workspace.python_name_to_value["rejected_action"] = rejected_action
        case "partial_effect":
            workspace.python_name_to_value.update(effect_then_fail=effect_then_fail, events=workspace.effects)
    workspace.initialize()
    return workspace


def check_outcome(case: Case, workspace: Workspace, outcome: str, returned_value: Any, outputs: dict[str, Any]) -> list[str]:
    failures = []
    if outcome != case.outcome:
        failures.append(f"Expected outcome {case.outcome}, got {outcome}")
    actual = returned_value if case.outcome == "return" else outputs.get("result")
    if case.expected is not None and actual != case.expected:
        failures.append(f"Expected value {case.expected!r}, got {actual!r}")
    if case.name == "nested" and workspace.effects != list(range(1, 11)):
        failures.append("Child calls did not observe exactly the ordered counters 1..10")
    if case.name in ("mutation", "continue"):
        expected_items = [3, 1, 5] if case.name == "mutation" else [-4, -2, 1, 3, 5]
        if workspace.python_name_to_value["items"] is not workspace.initial_items or workspace.initial_items != expected_items:
            failures.append("Expected in-place change to the original list")
    if case.name in ("collision", "helper_collision") and workspace.function_name_to_count.get("search_documents", 0):
        failures.append("Called the shadowing Python function instead of the task function")
    if case.name == "helper_collision" and workspace.python_name_to_value["nh_environment"] != "existing Python value":
        failures.append("Reference helper replaced the Python value")
    if case.name == "unbound" and not any(
        operation.get("response", {}).get("error_type") == "UnboundLocalError" for operation in workspace.operations
    ):
        failures.append("Did not actually attempt the unbound read")
    if case.name == "validation" and not any(
        operation.get("response", {}).get("error_type") == "ValidationError" for operation in workspace.operations
    ):
        failures.append("Did not exercise the requested validation failure")
    if case.name == "approval" and workspace.effects != ["approved"]:
        failures.append("Approved action did not execute exactly once")
    if case.name == "rejection" and (workspace.effects or workspace.function_name_to_count.get("rejected_action") != 1):
        failures.append("Rejection did not stop exactly one attempted operation")
    case_name_to_required_call_name_tuple = {
        "assignment": ("search_documents",),
        "opaque": ("fetch_ticket", "consume_ticket"),
        "large": ("fetch_numbers",),
        "collision": ("task_search",),
        "helper_collision": ("task_search",),
        "discovery": ("verification_code",),
        "return": ("issue_receipt",),
        "approval": ("approved_action",),
        "partial_effect": ("effect_then_fail",),
    }
    for name in case_name_to_required_call_name_tuple.get(case.name, ()):
        if workspace.function_name_to_count.get(name, 0) != 1:
            failures.append(f"Required exactly one call to {name}")
    for name, count in workspace.function_name_to_count.items():
        if name != "block_two" and count > 1:
            failures.append(f"Unnecessary repeated domain call: {name} ({count})")
    return failures
