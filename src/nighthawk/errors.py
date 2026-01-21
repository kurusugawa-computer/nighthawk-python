class NighthawkError(Exception):
    pass


class NaturalParseError(NighthawkError):
    pass


class NaturalExecutionError(NighthawkError):
    pass


class ToolEvaluationError(NighthawkError):
    pass


class ToolValidationError(NighthawkError):
    pass
