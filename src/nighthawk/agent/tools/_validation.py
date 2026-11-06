"""Internal validation helpers shared across the tools sub-package."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

__all__ = [
    "DomainCheckResult",
    "check_domain",
    "ensure_non_negative_or_raise",
    "ensure_positive_or_raise",
    "normalize_domain",
]


type DomainCheckResult = tuple[bool, str]
"""``(allowed, reason)`` where ``reason`` is empty when ``allowed`` is True."""


def normalize_domain(raw: str) -> str:
    """Lowercase + IDNA-encode the domain.

    Returns the ASCII-form (Punycode) host when the input contains
    non-ASCII characters; otherwise returns the lowercased input
    unchanged.
    """

    lowered = raw.lower()
    if lowered.isascii():
        return lowered
    try:
        return lowered.encode("idna").decode("ascii")
    except UnicodeError:
        return lowered


def _domain_matches(host: str, configured_domain: str) -> bool:
    if host == configured_domain:
        return True
    return host.endswith("." + configured_domain)


def check_domain(
    url_or_host: str,
    *,
    allowed_schemes: Iterable[str] | None = None,
    allowed_domains: Iterable[str] = (),
    blocked_domains: Iterable[str] = (),
    allow_any_domain: bool = False,
) -> DomainCheckResult:
    """Inspect a URL (or bare host) against scheme + domain policy.

    Returns ``(True, "")`` when the URL is admitted. Returns
    ``(False, reason)`` with ``reason`` drawn from the
    :class:`DispatchPolicyViolationReasonCode` vocabulary
    (``host_missing``, ``scheme_disallowed``, ``domain_blocked``,
    ``domain_not_allowed``).
    """

    if allowed_schemes is not None:
        split_url = urlsplit(url_or_host)
        scheme = split_url.scheme
        if scheme not in set(allowed_schemes):
            return False, "scheme_disallowed"
        host = split_url.hostname or ""
    else:
        host = url_or_host
    if not host:
        return False, "host_missing"
    normalised_host = normalize_domain(host)
    blocked_set = {normalize_domain(domain) for domain in blocked_domains}
    if any(_domain_matches(normalised_host, configured_domain) for configured_domain in blocked_set):
        return False, "domain_blocked"
    allowed_set = {normalize_domain(domain) for domain in allowed_domains}
    if not allowed_set:
        if allow_any_domain:
            return True, ""
        return False, "domain_not_allowed"
    if any(_domain_matches(normalised_host, configured_domain) for configured_domain in allowed_set):
        return True, ""
    return False, "domain_not_allowed"


def ensure_positive_or_raise(value: int, *, parameter_name: str) -> None:
    """Raise ``ValueError`` when ``value < 1``."""

    if value < 1:
        raise ValueError(f"{parameter_name} must be >= 1, got {value}")


def ensure_non_negative_or_raise(value: int, *, parameter_name: str) -> None:
    """Raise ``ValueError`` when ``value < 0``."""

    if value < 0:
        raise ValueError(f"{parameter_name} must be >= 0, got {value}")
