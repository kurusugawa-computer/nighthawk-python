"""Readable text for known operator metadata; ordinary results retain Python repr."""

from __future__ import annotations

import traceback

from ._operator_records import OperationDescriptor


def describe_operation(operation: OperationDescriptor) -> str:
    text = f"{operation.name}{operation.signature}\n  {operation.description}"
    if not operation.available:
        text += f"\n  Unavailable: {operation.unavailable_reason}"
    return text


def format_result(value: object) -> str:
    # Exact known types select a presentation format, not execution authority.
    # Do not parse repr text or probe arbitrary objects for a formatting protocol.
    if type(value) is OperationDescriptor:
        return describe_operation(value)
    if (type(value) is tuple or type(value) is list) and value and all(type(item) is OperationDescriptor for item in value):
        return "\n\n".join(describe_operation(item) for item in value)
    return repr(value)


def format_error(error: BaseException) -> str:
    """Keep causal summaries ahead of stacks that a bounded observation may omit."""
    formatted = traceback.TracebackException.from_exception(error)
    chain: list[traceback.TracebackException] = []
    current = formatted
    while current is not None:
        chain.append(current)
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    chain.reverse()
    summaries = []
    for item in chain[:4] + chain[-4:] if len(chain) > 8 else chain:
        summary = "".join(item.format_exception_only(show_group=True)).strip()
        summaries.append(summary[:500] + (" [incomplete]" if len(summary) > 500 else ""))
    if len(chain) > 8:
        summaries.insert(4, "[intermediate causes omitted]")
    return "Failure chain (cause first):\n" + "\n".join(summaries) + "\n\n" + "".join(formatted.format())
