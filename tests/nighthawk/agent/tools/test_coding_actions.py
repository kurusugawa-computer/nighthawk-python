"""Side-effecting coding capabilities bind authority in Python and declare perimeter effects."""

from __future__ import annotations

from pathlib import Path

import pytest

import nighthawk as nh
from nighthawk.agent import (
    CapabilityRejectedError,
    Grant,
    PerimeterEffectAttemptEvent,
    PerimeterPolicy,
    RunHandle,
    RunRejectedError,
    current_capabilities,
)
from nighthawk.agent.tools import CodingActionPolicy, build_coding_actions, build_default_file_writer
from nighthawk.agent.tools.coding_actions import CommandOutput, FilePatchOutcome, GeneratedImage, WebResponse
from nighthawk.agent.tools.requests import (
    AddFileOperation,
    CommandRequest,
    FilePatchRequest,
    HttpHeader,
    ImageGenerationRequest,
    UpdateFileOperation,
    WebRequest,
)
from nighthawk.runtime.step_request import StepCandidate, StepRequest
from nighthawk.testing import CallbackExecutor

from ..conftest import make_runner
from ..support import EchoResult, run_body


class _RecordingCommandExecutor:
    def __init__(self, output: CommandOutput | None = None) -> None:
        self.request_list: list[CommandRequest] = []
        self.output = output if output is not None else CommandOutput(exit_code=0, stdout_preview="ok")

    def execute(self, request: CommandRequest) -> CommandOutput:
        self.request_list.append(request)
        return self.output


class _RecordingWebRequestClient:
    def __init__(self) -> None:
        self.request_list: list[WebRequest] = []

    def send(self, request: WebRequest) -> WebResponse:
        self.request_list.append(request)
        return WebResponse(status_code=200, body_preview="x" * 10_000)


class _RecordingImageClient:
    def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        return GeneratedImage(media_type=f"image/{request.output_format}")


def _full_policy(workspace: Path) -> CodingActionPolicy:
    return CodingActionPolicy(
        allow_any_command_prefix=True,
        command_working_directory_allow_list=(workspace,),
        path_write_allow_list=(workspace,),
        allow_any_domain=True,
        maximum_preview_character_count=16,
    )


def _capability(grant: Grant, name: str):
    return grant.capabilities[name].function


def test_omitted_backends_omit_capabilities(tmp_path: Path) -> None:
    grant = build_coding_actions(policy=_full_policy(tmp_path), command_executor=_RecordingCommandExecutor())
    assert set(grant.capabilities) == {"run_command"}
    everything = build_coding_actions(
        policy=_full_policy(tmp_path),
        command_executor=_RecordingCommandExecutor(),
        file_writer=build_default_file_writer(),
        web_request_client=_RecordingWebRequestClient(),
        image_generation_client=_RecordingImageClient(),
    )
    assert set(everything.capabilities) == {"run_command", "apply_file_patch", "web_request", "generate_image"}
    assert everything.capabilities["run_command"].effect_footprint.perimeter_effect_class_set == {"destructive", "egress", "durable_write_back"}
    assert everything.capabilities["apply_file_patch"].effect_footprint.perimeter_effect_class_set == {"durable_write_back", "destructive"}
    assert everything.capabilities["web_request"].effect_footprint.perimeter_effect_class_set == {"egress"}


def test_run_command_policy_fails_closed_and_bounds_previews(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = _RecordingCommandExecutor(CommandOutput(exit_code=1, stdout_preview="a" * 40, stderr_preview="b"))
    run_command = _capability(build_coding_actions(policy=_full_policy(workspace), command_executor=executor), "run_command")
    output = run_command(CommandRequest(command=("echo", "hi"), working_directory=workspace))
    assert output == CommandOutput(exit_code=1, stdout_preview="a" * 16, stderr_preview="b", truncated=True)
    closed = _capability(
        build_coding_actions(policy=CodingActionPolicy(command_working_directory_allow_list=(workspace,)), command_executor=executor),
        "run_command",
    )
    with pytest.raises(CapabilityRejectedError) as captured:
        closed(CommandRequest(command=("echo", "hi"), working_directory=workspace))
    assert captured.value.reason_code == "command_prefix_not_allowed"
    denied = _capability(
        build_coding_actions(
            policy=CodingActionPolicy(
                allow_any_command_prefix=True, denied_command_prefixes=(("rm", "-rf"),), command_working_directory_allow_list=(workspace,)
            ),
            command_executor=executor,
        ),
        "run_command",
    )
    with pytest.raises(CapabilityRejectedError) as denied_capture:
        denied(CommandRequest(command=("rm", "-rf", "/"), working_directory=workspace))
    assert denied_capture.value.reason_code == "command_prefix_denied"
    with pytest.raises(CapabilityRejectedError) as outside:
        run_command(CommandRequest(command=("echo",), working_directory=tmp_path))
    assert outside.value.reason_code == "command_working_directory_disallowed"
    assert len(executor.request_list) == 1


def test_apply_file_patch_enforces_writable_roots_and_applies_atomically(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("keep", encoding="utf-8")
    target = workspace / "note.txt"
    target.write_text("old", encoding="utf-8")
    apply_file_patch = _capability(build_coding_actions(policy=_full_policy(workspace), file_writer=build_default_file_writer()), "apply_file_patch")
    outcome = apply_file_patch(
        FilePatchRequest(
            operations=(
                UpdateFileOperation(file_path=target, old_text="old", new_text="new"),
                AddFileOperation(file_path=workspace / "added.txt", new_text="hello\n"),
            )
        )
    )
    assert outcome == FilePatchOutcome(applied_operation_count=2)
    assert target.read_text(encoding="utf-8") == "new"
    with pytest.raises(CapabilityRejectedError) as captured:
        apply_file_patch(FilePatchRequest(operations=(UpdateFileOperation(file_path=outside / "secret.txt", old_text="keep", new_text="x"),)))
    assert captured.value.reason_code == "path_outside_allow_list"
    with pytest.raises(CapabilityRejectedError) as missing_parent:
        apply_file_patch(FilePatchRequest(operations=(AddFileOperation(file_path=workspace / "missing" / "x.txt", new_text="x"),)))
    assert missing_parent.value.reason_code == "path_parent_missing"
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "keep"


def test_web_request_policy_checks_scheme_domain_headers_and_body(tmp_path: Path) -> None:
    client = _RecordingWebRequestClient()
    policy = CodingActionPolicy(
        allowed_domains=frozenset({"example.com"}),
        blocked_domains=frozenset({"blocked.example.com"}),
        denied_request_header_names=frozenset({"authorization"}),
        maximum_request_body_bytes=4,
        maximum_preview_character_count=8,
    )
    web_request = _capability(build_coding_actions(policy=policy, web_request_client=client), "web_request")
    response = web_request(WebRequest(url="https://api.example.com/x"))
    assert response == WebResponse(status_code=200, body_preview="x" * 8, truncated=True)

    def code(request: WebRequest) -> str:
        with pytest.raises(CapabilityRejectedError) as captured:
            web_request(request)
        return captured.value.reason_code

    assert code(WebRequest(url="http://example.com/")) == "scheme_disallowed"
    assert code(WebRequest(url="https://other.com/")) == "domain_not_allowed"
    assert code(WebRequest(url="https://blocked.example.com/")) == "domain_blocked"
    assert code(WebRequest(url="https://example.com/", headers=(HttpHeader(name="Authorization", value="x"),))) == "header_name_denied"
    assert code(WebRequest(url="https://example.com/", method="POST", body="too large")) == "body_too_large"
    assert len(client.request_list) == 1


def test_generate_image_returns_the_typed_result(tmp_path: Path) -> None:
    generate_image = _capability(
        build_coding_actions(policy=_full_policy(tmp_path), image_generation_client=_RecordingImageClient()), "generate_image"
    )
    assert generate_image(ImageGenerationRequest(prompt="a harbour", output_format="webp")) == GeneratedImage(media_type="image/webp")


def test_rejection_messages_do_not_carry_request_payloads(tmp_path: Path) -> None:
    web_request = _capability(build_coding_actions(policy=CodingActionPolicy(), web_request_client=_RecordingWebRequestClient()), "web_request")
    with pytest.raises(CapabilityRejectedError) as captured:
        web_request(WebRequest(url="https://secrets.example.com/path?token=SECRET", body="payload"))
    rendered = str(captured.value)
    assert "domain_not_allowed" in rendered
    assert "secrets.example.com" not in rendered
    assert "SECRET" not in rendered
    assert "payload" not in rendered


@nh.function
def _edit(text: str) -> str:
    summary: str = ""
    """execute
    Read <text>, apply the patch, set <:summary>.
    """
    return summary


def test_coding_actions_inside_a_run_record_perimeter_attempts_and_require_policy(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    grant = build_coding_actions(policy=_full_policy(workspace), file_writer=build_default_file_writer())

    def handler(call: StepRequest) -> StepCandidate:
        apply_file_patch = current_capabilities()["apply_file_patch"]
        outcome = apply_file_patch({"operations": [{"verb": "add", "file_path": str(workspace / "new.txt"), "new_text": "hi\n"}]})  # type: ignore[operator]
        assert isinstance(outcome, FilePatchOutcome)
        return StepCandidate(binding_name_to_value={"summary": f"applied {outcome.applied_operation_count}"})

    handle = RunHandle()
    result = run_body(
        lambda run_input: EchoResult(value=_edit(run_input.text)),
        runner=make_runner(),
        step_executor=CallbackExecutor(handler),
        run_entry_grant=grant,
        handle=handle,
    )
    assert result.value == "applied 1"
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "hi\n"
    attempt = handle.ledger.select(PerimeterEffectAttemptEvent)[0].event
    assert isinstance(attempt, PerimeterEffectAttemptEvent)
    assert attempt.perimeter_effect_class_tuple == ("destructive", "durable_write_back")

    with pytest.raises(RunRejectedError) as captured:
        run_body(
            lambda run_input: EchoResult(),
            runner=make_runner(perimeter_policy=PerimeterPolicy.permitting("egress")),
            run_entry_grant=grant,
            composition_perimeter_policy=PerimeterPolicy.permitting("egress"),
        )
    assert captured.value.reason == "capability_outside_perimeter_policy"


def test_command_wrapper_keeps_optional_full_values(tmp_path: Path) -> None:
    retained = "a" * 1000 + "marker"
    backend = _RecordingCommandExecutor(
        CommandOutput(exit_code=0, stdout_preview=retained, stderr_preview="error", standard_output_text=retained, standard_error_text="error")
    )
    function = _capability(build_coding_actions(policy=_full_policy(tmp_path), command_executor=backend), "run_command")
    result = function(CommandRequest(command=("example",), working_directory=tmp_path))
    assert isinstance(result, CommandOutput)
    assert result.stdout_preview == retained[:16] and result.truncated
    assert result.standard_output_text is retained and result.standard_error_text == "error"
    preview_only = CommandOutput(exit_code=0, stdout_preview="partial", truncated=True)
    assert preview_only.standard_output_text is None


def test_web_wrapper_preserves_full_body(tmp_path: Path) -> None:
    class RetainingClient:
        def send(self, request: WebRequest) -> WebResponse:
            return WebResponse(status_code=200, body_preview="x" * 100, body_text="x" * 100 + "marker")

    function = _capability(build_coding_actions(policy=_full_policy(tmp_path), web_request_client=RetainingClient()), "web_request")
    result = function(WebRequest(url="https://example.com"))
    assert isinstance(result, WebResponse)
    assert result.body_preview == "x" * 16 and result.truncated
    assert result.body_text == "x" * 100 + "marker"
    assert WebResponse(status_code=200, body_preview="preview").body_text is None
