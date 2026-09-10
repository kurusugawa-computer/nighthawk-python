"""Bounded descriptions of known values, without inferring their source names."""

from __future__ import annotations

from .description import describe_callable, describe_public_members, safe_preview, type_name
from .discovery import DiscoveryResult, page_names


def inspect_value(value: object, *, cursor: str | None = None, max_items: int = 8) -> dict[str, object]:
    """Describe a value without invoking its application properties or methods.

    Member pages progress while the supported member-name listing is stable.
    An invalid cursor returns a recovery instruction. Metadata is best effort;
    missing metadata does not make the value inaccessible to Python.
    Name provenance and declared contracts belong to StepEnvironment instead.
    """
    members = describe_public_members(value)
    names = tuple(sorted(set(members.method_name_to_value) | set(members.field_name_to_value) | set(members.omitted_names)))
    page = page_names(names, source="value members", cursor=cursor, max_items=max_items)
    if page.error is not None:
        return {"error": page.error, "recovery": "Call inspect(value) again without a cursor and with max_items from 1 to 100."}
    description: dict[str, object] = {"type": type_name(value), "preview": safe_preview(value)}
    if callable(value):
        callable_description = describe_callable(value)
        description["signature"] = callable_description.signature_text
        description["documentation"] = callable_description.documentation
        description["async"] = callable_description.asynchronous
        if callable_description.signature is None:
            description["metadata_omission"] = "Callable signature is unavailable without executing application metadata."
        if any(type(annotation) is str for annotation in callable_description.annotations):
            description["annotation_note"] = "String annotations retain their spelling and were not evaluated."
    described_members: list[dict[str, object]] = []
    for name in page.names:
        if name in members.method_name_to_value:
            method = describe_callable(members.method_name_to_value[name])
            described_members.append({"name": name, "kind": "method", "signature": method.signature_text, "async": method.asynchronous})
        elif name in members.field_name_to_value:
            field_value = members.field_name_to_value[name]
            described_members.append(
                {"name": name, "kind": "field", "type": type_name(field_value), "preview": safe_preview(field_value, depth=1, max_items=4)}
            )
        else:
            described_members.append({"name": name, "kind": "opaque", "omission": "Descriptor or unbound slot; not read during inspection."})
    description["members"] = described_members
    description["next_cursor"] = page.next_cursor
    if page.next_cursor is not None:
        description["continuation"] = "Call inspect with the same value and next_cursor as cursor to read the next member page."
    elif not names:
        description["member_omission"] = "No supported public member metadata; use ordinary Python or a domain helper for further detail."
    return DiscoveryResult(description, item_count=len(page.names), repage=lambda count: inspect_value(value, cursor=cursor, max_items=count))
