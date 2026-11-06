"""Direct classification for host controls, independent of names or container traversal."""

from types import MethodType


class _OperatorControl:
    """Marker for controls that cannot become Agent capabilities through aliases."""


def is_operator_control(value: object) -> bool:
    return issubclass(type(value), _OperatorControl) or type(value) is MethodType and issubclass(type(value.__self__), _OperatorControl)
