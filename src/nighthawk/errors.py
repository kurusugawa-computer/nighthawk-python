from __future__ import annotations


class NighthawkError(Exception):
    pass


class NaturalParseError(NighthawkError):
    pass


class ExecutionError(NighthawkError):
    pass


class ToolEvaluationError(NighthawkError):
    pass


class ToolValidationError(NighthawkError):
    pass


class ToolRegistrationError(NighthawkError):
    pass
