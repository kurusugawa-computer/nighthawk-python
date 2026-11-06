"""Explicit source invocation and artifact export through the current grant.

These operations accept selected values and paths. They neither choose what to
save nor approve stored source, and require no conversation or Session.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter

from nighthawk.harness.context import current_capabilities
from nighthawk.harness.dispatch import AsyncGeneratedCallableReference, GeneratedCallableReference, dispatch
from nighthawk.harness.proposals import CodeProposal
from nighthawk.parts.files import AddFileOperation, FilePatchRequest
from nighthawk.parts.values import BasicDataValue

__all__ = ["SourceCandidate", "export_data", "export_source", "invoke_source"]


@dataclass(frozen=True)
class SourceCandidate:
    """Stored source data; neither callable authority nor approval evidence."""

    proposal: CodeProposal
    originating_run_id: str
    originating_cell_id: str | None = None


_BASIC_DATA = TypeAdapter(BasicDataValue)


async def invoke_source(
    candidate: SourceCandidate,
    *,
    export_name: str | None = None,
    argument_tuple: tuple[object, ...] = (),
    keyword_argument_name_to_value: Mapping[str, object] | None = None,
) -> object:
    """Dispatch selected source and await its result when the export is async.

    This intentionally rematerializes on each call. Use ``retain_function`` for
    a callable whose materialization is shared within each run.
    """
    proposal = candidate.proposal
    resolved_keyword_argument_name_to_value = dict(keyword_argument_name_to_value or {})
    if proposal.execution_intent == "definition_and_invocation":
        if export_name is not None or argument_tuple or resolved_keyword_argument_name_to_value:
            raise ValueError("a combined proposal already declares its entry and arguments")
        return dispatch(proposal).entry_result
    if export_name not in proposal.export_name_tuple:
        raise ValueError("source invocation must select a declared export")
    materialized = dispatch(proposal)
    function = materialized.export_name_to_value[export_name]
    if isinstance(function, AsyncGeneratedCallableReference):
        return await function(*argument_tuple, **resolved_keyword_argument_name_to_value)
    if isinstance(function, GeneratedCallableReference):
        return function(*argument_tuple, **resolved_keyword_argument_name_to_value)
    raise ValueError("the selected source export is not callable")


def _write(operations: tuple[AddFileOperation, ...]) -> tuple[str, ...]:
    writer = current_capabilities().get("apply_file_patch")
    if writer is None:
        raise LookupError("export requires the configured apply_file_patch capability")
    writer(FilePatchRequest(operations=operations))
    return tuple(str(operation.file_path) for operation in operations)


def export_source(candidate: SourceCandidate, file_path: str | Path) -> tuple[str, ...]:
    """Export exact source and dependency/provenance metadata through the writer."""
    path = Path(file_path)
    if path.suffix != ".py":
        raise ValueError("source export requires a .py path")
    proposal = candidate.proposal
    field_name_to_value = {
        "originating_run_id": candidate.originating_run_id,
        "originating_cell_id": candidate.originating_cell_id,
        "defining_source_identity": proposal.defining_source_identity,
        "export_name_tuple": proposal.export_name_tuple,
        "execution_intent": proposal.execution_intent,
        "dependency_tuple": [
            {"name": dependency.name, "kind": dependency.kind, "declared_type": str(dependency.declared_type)}
            for dependency in proposal.dependency_tuple
        ],
        "effect_footprint": proposal.effect_footprint.model_dump(mode="json"),
    }
    return _write(
        (
            AddFileOperation(file_path=path, new_text=proposal.source),
            AddFileOperation(file_path=Path(str(path) + ".json"), new_text=json.dumps(field_name_to_value, indent=2) + "\n"),
        )
    )


def export_data(value: object, file_path: str | Path) -> tuple[str, ...]:
    """Export selected JSON-compatible data through the current writer capability."""
    path = Path(file_path)
    if path.suffix != ".json":
        raise ValueError("data export requires a .json path")
    data = _BASIC_DATA.validate_python(value)
    return _write((AddFileOperation(file_path=path, new_text=json.dumps(data, allow_nan=False, indent=2) + "\n"),))
