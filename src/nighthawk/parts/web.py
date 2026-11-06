from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, field_validator

from nighthawk.harness._observability import emit_harness_event, harness_span
from nighthawk.harness.grants import Capability, Grant
from nighthawk.harness.perimeter import EffectFootprint
from nighthawk.parts._validation import check_domain, ensure_positive_or_raise, normalize_domain, reject_capability

__all__ = [
    "WebFetchClient",
    "WebFetchResult",
    "WebSearchClient",
    "WebSearchHit",
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
    ``url`` identifies the final fetched resource after any redirects.
    """

    url: str
    text: str
    citations: tuple[str, ...] = ()
    full_text: str | None = None
    truncated: bool | None = None


@runtime_checkable
class WebSearchClient(Protocol):
    """Trusted search backend that applies result-domain filters and the result-count cap.

    Domain filters include subdomains, with the block list taking precedence.
    An empty allow list permits any domain outside the block list.
    These filters select search results; they do not restrict the search provider's own endpoint.
    """

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
    """Fetch through a trusted backend that checks each actual destination before communication.

    Call ``check_url`` on the transport-normalized URL before every request, including redirects.
    Propagate a refusal without contacting that destination.
    """

    def fetch(
        self,
        url: str,
        *,
        focus: str,
        maximum_character_count: int,
        check_url: Callable[[str], None],
    ) -> WebFetchResult: ...


@dataclass(frozen=True, slots=True)
class _ResearchWebConfiguration:
    search_client: WebSearchClient
    fetch_client: WebFetchClient
    allowed_domains: frozenset[str]
    blocked_domains: frozenset[str]
    allowed_schemes: frozenset[str]
    maximum_query_character_count: int
    maximum_search_result_count: int
    maximum_fetch_character_count: int


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
            outside_configured = {
                domain for domain in caller_allowed_normalised if not check_domain(domain, allowed_domains=configured_allowed_domains)[0]
            }
            if outside_configured:
                raise ValueError(f"allowed_domains entries {sorted(outside_configured)!r} are not within the configured allow-list")
        effective_allowed = frozenset(caller_allowed_normalised) or configured_allowed_domains
        effective_blocked = configured_blocked_domains | caller_blocked_normalised
        with harness_span("capability.web_search", {"result_cap": maximum_result_count}):
            results = configuration.search_client.search(
                query,
                allowed_domains=tuple(sorted(effective_allowed)),
                blocked_domains=tuple(sorted(effective_blocked)),
                maximum_result_count=maximum_result_count,
            )
        emit_harness_event(
            "capability.web_search",
            {"result_count": len(results)},
        )
        return tuple(results)

    return web_search


def _make_web_fetch(configuration: _ResearchWebConfiguration) -> Callable[..., WebFetchResult]:
    def check_url(url: str) -> None:
        allowed, reason = check_domain(
            url,
            allowed_schemes=configuration.allowed_schemes,
            allowed_domains=configuration.allowed_domains,
            blocked_domains=configuration.blocked_domains,
            allow_any_domain=not configuration.allowed_domains,
        )
        if not allowed:
            raise ValueError(f"URL rejected by research_web policy: {reason}")

    def web_fetch(
        url: str,
        *,
        focus: str,
        maximum_character_count: int = 20_000,
    ) -> WebFetchResult:
        ensure_positive_or_raise(maximum_character_count, parameter_name="maximum_character_count")
        if maximum_character_count > configuration.maximum_fetch_character_count:
            raise ValueError(f"maximum_character_count {maximum_character_count} exceeds factory cap {configuration.maximum_fetch_character_count}")
        check_url(url)
        parsed = urlsplit(url)
        with harness_span(
            "capability.web_fetch",
            {"host": parsed.hostname, "scheme": parsed.scheme},
        ):
            result = configuration.fetch_client.fetch(
                url,
                focus=focus,
                maximum_character_count=maximum_character_count,
                check_url=check_url,
            )
        if len(result.text) > maximum_character_count:
            result = replace(result, text=result.text[:maximum_character_count], truncated=True)
        emit_harness_event(
            "capability.web_fetch",
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
    """Return ``web_search`` and ``web_fetch`` capabilities, both declaring egress.

    Domains include subdomains, use IDNA 2008, and ignore the DNS root dot.
    An empty allow list permits any domain outside the block list.
    Search callers may narrow the configured domain trees and add blocked domains.
    """

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


@dataclass(frozen=True, slots=True)
class WebRequestPolicy:
    """Select URL schemes, domain trees, request content, and preview bounds.

    Domains include their subdomains, use IDNA 2008, and ignore the DNS root dot.
    Blocked domains take precedence over allowed domains.
    These rules constrain URL hostnames; applications own DNS, proxies, and network isolation.
    """

    allowed_domains: frozenset[str] = frozenset()
    allow_any_domain: bool = False
    blocked_domains: frozenset[str] = frozenset()
    allowed_schemes: frozenset[str] = frozenset({"https"})
    denied_request_header_names: frozenset[str] = frozenset()
    maximum_request_body_bytes: int = 1_000_000
    maximum_preview_character_count: int = 4_000


type RequestMethod = Literal["GET", "POST", "PUT", "DELETE", "PATCH"]

_HEADER_FORBIDDEN_CODE_POINTS = frozenset(chr(code_point) for code_point in (*range(0x20), 0x7F))


class RequestHeader(BaseModel):
    """Validated HTTP header name and value; control characters are rejected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    value: str

    @field_validator("name", "value")
    @classmethod
    def _reject_control_characters(cls, raw: str) -> str:
        if any(character in _HEADER_FORBIDDEN_CODE_POINTS for character in raw):
            raise ValueError("RequestHeader name and value must not contain control characters")
        return raw


class WebRequest(BaseModel):
    """An HTTP request: URL, method, headers, optional body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    method: RequestMethod = "GET"
    headers: tuple[RequestHeader, ...] = ()
    body: str | None = None


class WebResponse(BaseModel):
    """A bounded HTTP preview and optional complete body retained by the backend."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status_code: int
    body_preview: str = ""
    body_text: str | None = None
    truncated: bool = False


@runtime_checkable
class WebRequestClient(Protocol):
    """Own connections and response capture; check each actual request before communication.

    Call ``check_request`` with the transport-normalized destination, method, headers, and body,
    including requests produced by redirects or authentication flows.
    Propagate a refusal without sending that request.
    """

    def send(self, request: WebRequest, *, check_request: Callable[[WebRequest], None]) -> WebResponse: ...


def web_request_capability(*, policy: WebRequestPolicy, web_request_client: WebRequestClient) -> Capability:
    denied_header_names_lowered = {name.lower() for name in policy.denied_request_header_names}

    def check_request(request: WebRequest) -> None:
        allowed, reason = check_domain(
            request.url,
            allowed_schemes=policy.allowed_schemes,
            allowed_domains=policy.allowed_domains,
            blocked_domains=policy.blocked_domains,
            allow_any_domain=policy.allow_any_domain,
        )
        if not allowed:
            raise reject_capability("web_request", reason)
        for header in request.headers:
            if header.name.lower() in denied_header_names_lowered:
                raise reject_capability("web_request", "header_name_denied")
        if request.body is not None and len(request.body.encode("utf-8")) > policy.maximum_request_body_bytes:
            raise reject_capability("web_request", "body_too_large")

    def web_request(request: WebRequest) -> WebResponse:
        """Send one HTTP request to an allow-listed host over an allow-listed scheme."""

        check_request(request)
        parsed = urlsplit(request.url)
        with harness_span("capability.web_request", {"method": request.method, "host": parsed.hostname or ""}):
            response = web_request_client.send(request, check_request=check_request)
        body_preview, truncated = (
            response.body_preview[: policy.maximum_preview_character_count],
            len(response.body_preview) > policy.maximum_preview_character_count,
        )
        emit_harness_event("capability.web_request", {"status_code": response.status_code})
        return WebResponse(
            status_code=response.status_code, body_preview=body_preview, body_text=response.body_text, truncated=response.truncated or truncated
        )

    domain_description = "any domain not on the block list" if policy.allow_any_domain else f"{len(policy.allowed_domains)} allow-listed domain(s)"
    return Capability(
        name="web_request",
        function=web_request,
        authority_boundary=f"HTTP {', '.join(sorted(policy.allowed_schemes))} to {domain_description}",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset({"egress"})),
    )
