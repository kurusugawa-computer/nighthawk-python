"""Limited CPython effect checks for run interiors, without a sandbox claim.

The hook observes selected Python audit events and rejects unmediated effects.
Native extensions, unaudited descriptor operations, memory mutation, and work
outside the propagated execution context are not covered by this mechanism.
Host gateways authorize only their own dynamic execution and restore on exit.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from importlib import _bootstrap_external
from typing import TYPE_CHECKING

from nighthawk.harness.errors import PerimeterEffectRejectedError
from nighthawk.harness.perimeter import PERIMETER_EFFECT_CLASS_TUPLE, PerimeterEffectClass

if TYPE_CHECKING:
    from nighthawk.harness._host import Host

_active_host: ContextVar[Host | None] = ContextVar("nighthawk_audited_host", default=None)


@dataclass
class _Authorization:
    effects: frozenset[PerimeterEffectClass]
    active: bool = True


_authorization: ContextVar[_Authorization | None] = ContextVar("nighthawk_audit_authorization", default=None)
_recording: ContextVar[bool] = ContextVar("nighthawk_recording_audit_rejection", default=False)
_installation_lock = threading.Lock()
_installed = False
_probe = object()
_probe_seen = False
_ALL_EFFECTS = frozenset(PERIMETER_EFFECT_CLASS_TUPLE)
_WRITE = frozenset[PerimeterEffectClass](("durable_write_back",))
_DELETE = frozenset[PerimeterEffectClass](("destructive",))
_EGRESS = frozenset[PerimeterEffectClass](("egress",))
_CACHE_WRITE_CODE_SET = frozenset(
    (
        _bootstrap_external._write_atomic.__code__,  # pyright: ignore[reportAttributeAccessIssue]
        _bootstrap_external.SourceFileLoader.set_data.__code__,
    )
)


def _effects(event: str, arguments: tuple[object, ...]) -> frozenset[PerimeterEffectClass]:
    if event == "open" and len(arguments) >= 3:
        flags = arguments[2]
        if type(flags) is int and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            return _WRITE
    if event in ("os.remove", "os.rmdir"):
        return _DELETE
    if event in ("os.rename", "os.replace"):
        return _WRITE | _DELETE
    if event in ("os.mkdir", "os.link", "os.symlink", "os.truncate", "os.chmod", "os.chown", "os.utime", "os.setxattr", "os.removexattr"):
        return _WRITE
    if event in (
        "socket.connect",
        "socket.connect_ex",
        "socket.bind",
        "socket.sendto",
        "socket.sendmsg",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.gethostbyaddr",
    ):
        return _EGRESS
    if event in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.fork", "os.forkpty", "os.spawn"):
        return _ALL_EFFECTS
    if event in ("os.kill", "os.killpg"):
        return _DELETE
    return frozenset()


def _hook(event: str, arguments: tuple[object, ...]) -> None:
    global _probe_seen
    if event == "nighthawk.audit_probe" and len(arguments) == 1 and arguments[0] is _probe:
        _probe_seen = True
        return
    host = _active_host.get()
    if host is None or _recording.get():
        return
    effects = _effects(event, arguments)
    authorization = _authorization.get()
    if not effects or (authorization is not None and authorization.active and effects <= authorization.effects):
        return
    if event in ("open", "os.mkdir") and sys._getframe(1).f_code in _CACHE_WRITE_CODE_SET:
        # CPython already treats an unwritable bytecode cache as an ordinary import.
        # Refuse the cache effect without rejecting module loading or granting its body any effects.
        raise PermissionError("Bytecode cache writes are unavailable inside an agent run")
    context = host.current_context()
    node_id = context.node.node_id if context is not None else host.run_root.node_id
    token = _recording.set(True)
    try:
        host.record_anomaly("unmediated_perimeter_effect", node_id=node_id, detail=f"Python audit event {event}; effects={','.join(sorted(effects))}")
    finally:
        _recording.reset(token)
    raise PerimeterEffectRejectedError(capability_name=event, reason="unmediated_python_effect", attribution_node_id=node_id)


@contextmanager
def observe_run(host: Host) -> Generator[None]:
    global _installed
    with _installation_lock:
        if not _installed:
            sys.addaudithook(_hook)
            sys.audit("nighthawk.audit_probe", _probe)
            if not _probe_seen:
                raise RuntimeError("The Nighthawk Python audit hook could not be installed")
            _installed = True
    token = _active_host.set(host)
    try:
        yield
    finally:
        _active_host.reset(token)


@contextmanager
def permit_effects(effects: frozenset[PerimeterEffectClass]) -> Generator[None]:
    authorization = _Authorization(effects)
    token = _authorization.set(authorization)
    try:
        yield
    finally:
        authorization.active = False
        _authorization.reset(token)


def gateway_allowance_active() -> bool:
    """Return whether a host gateway currently authorizes direct effects in this context."""
    authorization = _authorization.get()
    return authorization is not None and authorization.active and bool(authorization.effects)


@contextmanager
def host_service_scope() -> Generator[None]:
    """Configured host infrastructure, such as durable audit storage, is outside agent authority."""
    with permit_effects(_ALL_EFFECTS):
        yield


@contextmanager
def capability_effect_scope(effects: frozenset[PerimeterEffectClass]) -> Generator[None]:
    """An admitted perimeter capability owns its implementation, including temporary-file cleanup.

    The host checks its semantic footprint and persists the attempt before entry.
    Low-level audit events do not redefine that contract as a system-call footprint.
    A capability without perimeter effects still cannot directly perform observed perimeter operations.
    """
    with permit_effects(_ALL_EFFECTS if effects else frozenset()):
        yield
