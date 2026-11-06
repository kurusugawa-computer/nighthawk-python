from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

import idna

from nighthawk.harness._observability import emit_harness_event
from nighthawk.harness.errors import CapabilityRejectedError


def normalize_domain(raw: str) -> str:
    """Use IDNA 2008 like HTTP transports and ignore the DNS root dot."""

    lowered = raw.lower().removesuffix(".")
    if lowered.isascii():
        return lowered
    return idna.encode(lowered).decode("ascii")


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
) -> tuple[bool, str]:
    """Return admission and its refusal reason for a URL or bare host."""

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
    if value < 1:
        raise ValueError(f"{parameter_name} must be >= 1, got {value}")


def ensure_non_negative_or_raise(value: int, *, parameter_name: str) -> None:
    if value < 0:
        raise ValueError(f"{parameter_name} must be >= 0, got {value}")


def reject_capability(capability_name: str, reason_code: str) -> CapabilityRejectedError:
    emit_harness_event("capability_rejected", {"capability_name": capability_name, "reason_code": reason_code})
    return CapabilityRejectedError(capability_name=capability_name, reason_code=reason_code)
