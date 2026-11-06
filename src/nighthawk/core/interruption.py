"""Preserve primary interruption identity and inspectable cleanup failures."""

from __future__ import annotations


def chain_cleanup_errors(primary: BaseException, *errors: BaseException) -> None:
    causes: list[BaseException] = []
    if primary.__cause__ is not None:
        causes.append(primary.__cause__)
    for error in errors:
        if error is not primary and all(error is not cause for cause in causes):
            causes.append(error)
    if len(causes) == 1:
        primary.__cause__ = causes[0]
    elif causes:
        primary.__cause__ = BaseExceptionGroup("Errors during owned cleanup", causes)
    if causes:
        primary.__suppress_context__ = True
