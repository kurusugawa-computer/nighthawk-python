class NighthawkError(Exception):
    """Base exception for Nighthawk's public error contracts.

    The concrete error and its execution boundary determine recovery, not this base class.
    Invalid arguments may raise ordinary Python or Pydantic validation errors;
    application and provider exceptions can propagate with their original types.
    """
