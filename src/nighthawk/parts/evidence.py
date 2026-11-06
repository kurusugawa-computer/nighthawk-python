from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from nighthawk.harness.evidence import EvidenceReference, select_subtree_entries
from nighthawk.harness.ledger import LedgerEntry


class _FrozenRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidenceItem(_FrozenRecord):
    reference: EvidenceReference
    sequence: int
    excerpt: str
    incomplete: bool


class EvidenceQuery(_FrozenRecord):
    starting_sequence: int = Field(default=0, ge=0)
    event_kind_tuple: tuple[str, ...] = ()
    attribution_node_id: str | None = None


class EvidenceBounds(_FrozenRecord):
    maximum_event_count: int = Field(default=20, gt=0)
    maximum_character_count: int = Field(default=6000, gt=0)
    maximum_excerpt_character_count: int = Field(default=1000, gt=0)


class EvidenceProjection(_FrozenRecord):
    """A captured horizon with bounded excerpts and advancing event pagination.

    Character limits count excerpt text. Identity metadata remains explicit.
    Incomplete excerpts cannot be recovered merely by requesting the next page.
    """

    run_id: str
    horizon_sequence: int | None
    evidence_tuple: tuple[EvidenceItem, ...]
    next_starting_sequence: int | None
    incomplete: bool


def _plain_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return {name: _plain_value(getattr(value, name)) for name in type(value).model_fields}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain_value(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        if all(type(key) is str for key in value):
            return {key: _plain_value(item) for key, item in value.items()}
        return {"entry_tuple": [[_plain_value(key), _plain_value(item)] for key, item in value.items()]}
    if isinstance(value, tuple | list):
        return [_plain_value(item) for item in value]
    if isinstance(value, frozenset | set):
        return sorted((_plain_value(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, bytes):
        return value.hex()
    return f"<{type(value).__module__}.{type(value).__qualname__}>"


def project_evidence(entries: tuple[LedgerEntry, ...], request: EvidenceQuery, *, run_id: str, bounds: EvidenceBounds) -> EvidenceProjection:
    """Summarize eligible events without allocation arithmetic or implicit history.

    A page must fit one complete bounded excerpt (possibly itself shortened by
    the per-excerpt limit). Otherwise reject the unusable total bound. Pagination
    advances past the last examined event when eligible events remain.
    Mappings with non-string keys use explicit key/value pairs in their excerpts;
    rendering cannot merge distinct observations by stringifying their keys.
    """
    selected = select_subtree_entries(entries, request.attribution_node_id) if request.attribution_node_id is not None else entries
    eligible = tuple(
        entry
        for entry in sorted(selected, key=lambda entry: entry.sequence)
        if entry.sequence >= request.starting_sequence and (not request.event_kind_tuple or entry.event.kind in request.event_kind_tuple)
    )
    evidence_list: list[EvidenceItem] = []
    character_count = 0
    for entry in eligible:
        field_name_to_value = {
            name: _plain_value(getattr(entry.event, name)) for name in type(entry.event).model_fields if name not in _OMITTED_HOST_FIELD_SET
        }
        text = json.dumps(field_name_to_value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        excerpt = text[: bounds.maximum_excerpt_character_count]
        if character_count + len(excerpt) > bounds.maximum_character_count or len(evidence_list) >= bounds.maximum_event_count:
            if not evidence_list:
                raise ValueError("maximum_character_count cannot fit one bounded excerpt")
            break
        evidence_list.append(
            EvidenceItem(
                reference=EvidenceReference(run_id=run_id, event_id=entry.event_id),
                sequence=entry.sequence,
                excerpt=excerpt,
                incomplete=len(excerpt) < len(text) or bool(_OMITTED_HOST_FIELD_SET.intersection(type(entry.event).model_fields)),
            )
        )
        character_count += len(excerpt)
    remaining = len(evidence_list) < len(eligible)
    return EvidenceProjection(
        run_id=run_id,
        horizon_sequence=max((entry.sequence for entry in entries), default=None),
        evidence_tuple=tuple(evidence_list),
        next_starting_sequence=evidence_list[-1].sequence + 1 if remaining else None,
        incomplete=remaining or any(evidence.incomplete for evidence in evidence_list),
    )


_OMITTED_HOST_FIELD_SET = frozenset({"executor_usage", "executor_step_count", "resource_allocation_identity", "recursion_depth"})
