"""Compose Natural functions with ordinary Python execution."""

from nighthawk._execution import run, scope
from nighthawk.core.configuration import UNSET, Extend, Merge
from nighthawk.core.natural.decorator import function
from nighthawk.errors import NighthawkError

__all__ = ["function", "run", "scope", "UNSET", "Extend", "Merge", "NighthawkError"]
