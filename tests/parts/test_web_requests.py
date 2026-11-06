from __future__ import annotations

from collections.abc import Callable

import pytest

from nighthawk.harness.errors import CapabilityRejectedError
from nighthawk.parts.web import RequestHeader, WebRequest, WebRequestPolicy, WebResponse, web_request_capability


class _RecordingWebRequestClient:
    def __init__(self) -> None:
        self.request_list: list[WebRequest] = []

    def send(self, request: WebRequest, *, check_request: Callable[[WebRequest], None]) -> WebResponse:
        check_request(request)
        self.request_list.append(request)
        return WebResponse(status_code=200, body_preview="x" * 10_000)


def test_web_request_policy_checks_scheme_domain_headers_and_body() -> None:
    client = _RecordingWebRequestClient()
    policy = WebRequestPolicy(
        allowed_domains=frozenset({"example.com"}),
        blocked_domains=frozenset({"blocked.example.com"}),
        denied_request_header_names=frozenset({"authorization"}),
        maximum_request_body_bytes=4,
        maximum_preview_character_count=8,
    )
    capability = web_request_capability(policy=policy, web_request_client=client)
    assert capability.name == "web_request"
    assert capability.effect_footprint.perimeter_effect_class_set == {"egress"}
    web_request = capability.function
    response = web_request(WebRequest(url="https://api.example.com/x"))
    assert response == WebResponse(status_code=200, body_preview="x" * 8, truncated=True)

    def code(request: WebRequest) -> str:
        with pytest.raises(CapabilityRejectedError) as captured:
            web_request(request)
        return captured.value.reason_code

    assert code(WebRequest(url="http://example.com/")) == "scheme_disallowed"
    assert code(WebRequest(url="https://other.com/")) == "domain_not_allowed"
    assert code(WebRequest(url="https://blocked.example.com/")) == "domain_blocked"
    assert code(WebRequest(url="https://example.com/", headers=(RequestHeader(name="Authorization", value="x"),))) == "header_name_denied"
    assert code(WebRequest(url="https://example.com/", method="POST", body="too large")) == "body_too_large"
    assert len(client.request_list) == 1


def test_rejection_messages_do_not_carry_request_payloads() -> None:
    web_request = web_request_capability(policy=WebRequestPolicy(), web_request_client=_RecordingWebRequestClient()).function
    with pytest.raises(CapabilityRejectedError) as captured:
        web_request(WebRequest(url="https://secrets.example.com/path?token=SECRET", body="payload"))
    rendered = str(captured.value)
    assert "domain_not_allowed" in rendered
    assert "secrets.example.com" not in rendered
    assert "SECRET" not in rendered
    assert "payload" not in rendered


def test_web_wrapper_preserves_full_body() -> None:
    class RetainingClient:
        def send(self, request: WebRequest, *, check_request: Callable[[WebRequest], None]) -> WebResponse:
            check_request(request)
            return WebResponse(status_code=200, body_preview="x" * 100, body_text="x" * 100 + "marker")

    function = web_request_capability(
        policy=WebRequestPolicy(allow_any_domain=True, maximum_preview_character_count=16), web_request_client=RetainingClient()
    ).function
    result = function(WebRequest(url="https://example.com"))
    assert isinstance(result, WebResponse)
    assert result.body_preview == "x" * 16 and result.truncated
    assert result.body_text == "x" * 100 + "marker"
    assert WebResponse(status_code=200, body_preview="preview").body_text is None


@pytest.mark.parametrize(
    "redirected,reason",
    [
        (WebRequest(url="https://outside.example/"), "domain_not_allowed"),
        (WebRequest(url="http://allowed.example/"), "scheme_disallowed"),
        (WebRequest(url="https://allowed.example/", headers=(RequestHeader(name="Authorization", value="secret"),)), "header_name_denied"),
        (WebRequest(url="https://allowed.example/", method="POST", body="excess"), "body_too_large"),
    ],
)
def test_backend_checks_followup_requests_before_sending(redirected: WebRequest, reason: str) -> None:
    sent: list[WebRequest] = []

    class RedirectingClient:
        def send(self, request: WebRequest, *, check_request: Callable[[WebRequest], None]) -> WebResponse:
            for candidate in (request, redirected):
                check_request(candidate)
                sent.append(candidate)
            return WebResponse(status_code=200)

    capability = web_request_capability(
        policy=WebRequestPolicy(
            allowed_domains=frozenset({"allowed.example"}), denied_request_header_names=frozenset({"authorization"}), maximum_request_body_bytes=4
        ),
        web_request_client=RedirectingClient(),
    )
    initial = WebRequest(url="https://allowed.example/")
    with pytest.raises(CapabilityRejectedError) as captured:
        capability.function(initial)
    assert captured.value.reason_code == reason
    assert sent == [initial]
