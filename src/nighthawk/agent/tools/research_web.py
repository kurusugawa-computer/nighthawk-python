"""Read-only web-research capabilities.

:func:`build_research_web` binds caller-supplied :class:`WebSearchClient` and
:class:`WebFetchClient` backends together with URL, domain, and size policy and
returns a grant of ``web_search`` and ``web_fetch`` capabilities. Both declare
the ``egress`` perimeter class because a request leaves the run even when the
operation is read-only, so the effective perimeter policy must permit egress.

Allow-list semantics: an empty ``allowed_domains`` means unconstrained (the
block list still applies), because read-only research is the lowest-risk
egress and a fail-closed default would make exploratory research unusable.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from .._grants import Capability, Grant
from .._observability import agent_span, emit_agent_event
from .._perimeter import EffectFootprint
from ._validation import check_domain, ensure_positive_or_raise, normalize_domain

__all__ = [
    "WebFetchClient",
    "WebFetchResult",
    "WebSearchClient",
    "WebSearchHit",
    "build_default_web_fetch_client",
    "build_research_web",
]


@dataclass(frozen=True, slots=True)
class WebSearchHit:
    """A single search result."""

    title: str
    url: str
    snippet: str


@dataclass(frozen=True, slots=True)
class WebFetchResult:
    """A fetched preview with optional citations and complete retained text.

    ``truncated`` describes known preview omission, independently of full-text retention;
    ``None`` means the fetch client does not report it.
    A complete empty response may be retained as ``full_text=""``.
    """

    url: str
    text: str
    citations: tuple[str, ...] = ()
    full_text: str | None = None
    truncated: bool | None = None


@runtime_checkable
class WebSearchClient(Protocol):
    """Caller-supplied web-search backend."""

    def search(
        self,
        query: str,
        *,
        allowed_domains: Sequence[str],
        blocked_domains: Sequence[str],
        maximum_result_count: int,
    ) -> Sequence[WebSearchHit]: ...


@runtime_checkable
class WebFetchClient(Protocol):
    """Caller-supplied URL-fetch backend."""

    def fetch(
        self,
        url: str,
        *,
        focus: str,
        maximum_character_count: int,
    ) -> WebFetchResult: ...


@dataclass(frozen=True, slots=True)
class _ResearchWebConfiguration:
    search_client: WebSearchClient
    fetch_client: WebFetchClient
    allowed_domains: frozenset[str] = field(default_factory=frozenset)
    blocked_domains: frozenset[str] = field(default_factory=frozenset)
    allowed_schemes: frozenset[str] = field(default_factory=lambda: frozenset({"https"}))
    maximum_query_character_count: int = 1024
    maximum_search_result_count: int = 50
    maximum_fetch_character_count: int = 50_000


def _make_web_search(configuration: _ResearchWebConfiguration) -> Callable[..., Sequence[WebSearchHit]]:
    configured_allowed_domains = frozenset(normalize_domain(domain) for domain in configuration.allowed_domains)
    configured_blocked_domains = frozenset(normalize_domain(domain) for domain in configuration.blocked_domains)

    def web_search(
        query: str,
        *,
        allowed_domains: Sequence[str] = (),
        blocked_domains: Sequence[str] = (),
        maximum_result_count: int = 10,
    ) -> Sequence[WebSearchHit]:
        ensure_positive_or_raise(maximum_result_count, parameter_name="maximum_result_count")
        if len(query) > configuration.maximum_query_character_count:
            raise ValueError(f"query length {len(query)} exceeds maximum {configuration.maximum_query_character_count}")
        if maximum_result_count > configuration.maximum_search_result_count:
            raise ValueError(f"maximum_result_count {maximum_result_count} exceeds factory cap {configuration.maximum_search_result_count}")
        caller_allowed_normalised = {normalize_domain(domain) for domain in allowed_domains}
        caller_blocked_normalised = {normalize_domain(domain) for domain in blocked_domains}
        if configured_allowed_domains and caller_allowed_normalised:
            outside_configured = caller_allowed_normalised - configured_allowed_domains
            if outside_configured:
                raise ValueError(f"allowed_domains entries {sorted(outside_configured)!r} are not within the configured allow-list")
        if configured_allowed_domains:
            effective_allowed: frozenset[str] = frozenset(caller_allowed_normalised) if caller_allowed_normalised else configured_allowed_domains
        else:
            effective_allowed = frozenset(caller_allowed_normalised)
        effective_blocked = configured_blocked_domains | caller_blocked_normalised
        with agent_span("tool.web_search", {"result_cap": maximum_result_count}):
            results = configuration.search_client.search(
                query,
                allowed_domains=tuple(sorted(effective_allowed)),
                blocked_domains=tuple(sorted(effective_blocked)),
                maximum_result_count=maximum_result_count,
            )
        emit_agent_event(
            "tool.web_search",
            {"result_count": len(results)},
        )
        return tuple(results)

    return web_search


def _make_web_fetch(configuration: _ResearchWebConfiguration) -> Callable[..., WebFetchResult]:
    def web_fetch(
        url: str,
        *,
        focus: str,
        maximum_character_count: int = 20_000,
    ) -> WebFetchResult:
        ensure_positive_or_raise(maximum_character_count, parameter_name="maximum_character_count")
        if maximum_character_count > configuration.maximum_fetch_character_count:
            raise ValueError(f"maximum_character_count {maximum_character_count} exceeds factory cap {configuration.maximum_fetch_character_count}")
        parsed = urlsplit(url)
        if not parsed.hostname:
            raise ValueError("URL has no hostname")
        # research_web allows any domain when no allow-list is configured (read-only intent);
        # use allow_any_domain=True when configuration.allowed_domains is empty.
        allowed, reason = check_domain(
            url,
            allowed_schemes=configuration.allowed_schemes,
            allowed_domains=configuration.allowed_domains,
            blocked_domains=configuration.blocked_domains,
            allow_any_domain=not configuration.allowed_domains,
        )
        if not allowed:
            raise ValueError(f"URL rejected by research_web policy: {reason}")
        with agent_span(
            "tool.web_fetch",
            {"host": parsed.hostname, "scheme": parsed.scheme},
        ):
            result = configuration.fetch_client.fetch(
                url,
                focus=focus,
                maximum_character_count=maximum_character_count,
            )
        if len(result.text) > maximum_character_count:
            result = replace(result, text=result.text[:maximum_character_count], truncated=True)
        emit_agent_event(
            "tool.web_fetch",
            {"host": parsed.hostname or "", "byte_size": len(result.text)},
        )
        return result

    return web_fetch


def build_research_web(
    *,
    search_client: WebSearchClient,
    fetch_client: WebFetchClient,
    allowed_domains: frozenset[str] = frozenset(),
    blocked_domains: frozenset[str] = frozenset(),
    allowed_schemes: frozenset[str] = frozenset({"https"}),
    maximum_query_character_count: int = 1024,
    maximum_search_result_count: int = 50,
    maximum_fetch_character_count: int = 50_000,
) -> Grant:
    """Return a grant of ``web_search`` and ``web_fetch`` capabilities."""

    configuration = _ResearchWebConfiguration(
        search_client=search_client,
        fetch_client=fetch_client,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
        allowed_schemes=allowed_schemes,
        maximum_query_character_count=maximum_query_character_count,
        maximum_search_result_count=maximum_search_result_count,
        maximum_fetch_character_count=maximum_fetch_character_count,
    )
    domain_description = ", ".join(sorted(allowed_domains)) if allowed_domains else "any domain not on the block list"
    footprint = EffectFootprint(perimeter_effect_class_set=frozenset({"egress"}))
    return Grant(
        (
            Capability(
                name="web_search",
                function=_make_web_search(configuration),
                authority_boundary=f"read-only web search limited to {domain_description}",
                effect_footprint=footprint,
            ),
            Capability(
                name="web_fetch",
                function=_make_web_fetch(configuration),
                authority_boundary=f"read-only {', '.join(sorted(allowed_schemes))} fetch limited to {domain_description}",
                effect_footprint=footprint,
            ),
        )
    )


# Re-export at module bottom to avoid the circular import that would arise
# if ``_default_web_backends`` were imported at module top: that module
# imports ``WebFetchResult`` from this one.
from ._default_web_backends import build_default_web_fetch_client as build_default_web_fetch_client  # noqa: E402
