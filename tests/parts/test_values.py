import asyncio
from dataclasses import dataclass

import pytest

from nighthawk.core.step_outcome import Pass
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.parts.values import read_text_window, search_text, select_sequence_window


def test_unicode_line_and_character_windows() -> None:
    value = "日本語\n" + "長" * 10000 + "\nlast"
    first = read_text_window(value, maximum_line_count=1)
    assert first.text == "日本語\n" and first.next_starting_character == 4
    second = read_text_window(value, starting_character=4, maximum_character_count=9)
    assert second.text == "長" * 9 and second.next_starting_character == 13
    last = read_text_window(value, starting_character=10005, maximum_line_count=1)
    assert last.text == "last" and not last.incomplete
    for start in (len(value), len(value) + 3):
        page = read_text_window(value, starting_character=start)
        assert page.text == "" and page.next_starting_character is None and not page.incomplete


def test_overlapping_search_pages_advance_even_without_hits() -> None:
    position = 0
    starts = []
    pages = []
    while True:
        page = search_text("ababa", "aba", starting_character=position, maximum_scanned_character_count=3)
        pages.append(page)
        starts.extend(match.starting_character for match in page.match_tuple)
        assert page.scanned_character_count <= 3
        if page.next_starting_character is None:
            break
        assert page.next_starting_character > position
        position = page.next_starting_character
    assert starts == [0, 2]
    assert pages[1].match_tuple == () and pages[1].incomplete
    assert pages[0].match_tuple[0].ending_character == 3


@pytest.mark.parametrize("query", ["a", "aa", "aba", "日本"])
@pytest.mark.parametrize("limit", [1, 2, 5, 20])
def test_paginated_search_equals_all_literal_candidate_starts(query: str, limit: int) -> None:
    value = "aaababa日本日本aaaa"
    expected = [position for position in range(len(value)) if value.startswith(query, position)]
    actual = []
    position = 0
    while True:
        page = search_text(value, query, starting_character=position, maximum_scanned_character_count=max(len(query), limit), maximum_match_count=1)
        actual.extend(match.starting_character for match in page.match_tuple)
        if page.next_starting_character is None:
            break
        assert page.next_starting_character > position
        position = page.next_starting_character
    assert actual == expected


def test_search_preview_bounds_and_terminal_offsets() -> None:
    page = search_text("界" * 1000, "界", maximum_match_count=1, maximum_scanned_character_count=250)
    assert len(page.match_tuple[0].preview) == 200
    assert page.next_starting_character == 1
    assert search_text("x", "long").next_starting_character is None
    assert search_text("x", "x", starting_character=4).scanned_character_count == 0


@pytest.mark.parametrize(
    "operation",
    [
        lambda: read_text_window("x", starting_character=-1),
        lambda: read_text_window("x", maximum_character_count=0),
        lambda: read_text_window("x", maximum_line_count=0),
        lambda: search_text("x", ""),
        lambda: search_text("xxx", "xx", maximum_scanned_character_count=1),
        lambda: search_text("x", "x", maximum_match_count=0),
        lambda: search_text("x", "x", starting_character=-1),
        lambda: select_sequence_window([1], starting_index=-1),
        lambda: select_sequence_window([1], maximum_item_count=0),
    ],
)
def test_unusable_page_bounds_fail(operation) -> None:
    with pytest.raises(ValueError):
        operation()


def test_sequence_windows_preserve_shared_aliases() -> None:
    shared = [1]
    value = [shared, shared, [3]]
    page = select_sequence_window(value, maximum_item_count=1)
    assert page.item_tuple[0] is shared and page.next_starting_index == 1
    shared.append(2)
    assert page.item_tuple[0] == [1, 2]
    assert select_sequence_window(value, starting_index=1).item_tuple[0] is shared
    for start in (len(value), len(value) + 1):
        page = select_sequence_window(value, starting_index=start)
        assert page.item_tuple == () and not page.incomplete


@dataclass
class _Document:
    content: str


def test_async_producers_return_retained_values_for_later_queries() -> None:
    calls = []

    async def samples() -> tuple[int, ...]:
        calls.append("samples")
        return tuple(range(10000))

    async def document() -> _Document:
        calls.append("document")
        return _Document("." * 10000 + "marker")

    async def work() -> None:
        sample_values, retained_document = await asyncio.gather(samples(), document())
        assert 9999 not in select_sequence_window(sample_values).item_tuple
        assert select_sequence_window(sample_values, starting_index=9999).item_tuple == (9999,)
        assert "marker" not in read_text_window(retained_document.content).text
        assert search_text(retained_document.content, "marker", starting_character=9999).match_tuple[0].starting_character == 10000

    asyncio.run(work())
    assert calls == ["samples", "document"]


def test_executor_prompt_bounds_large_retained_member() -> None:
    import nighthawk as nh
    from nighthawk.adapters.pydantic_ai.configuration import StepContextLimits, StepExecutorConfiguration
    from nighthawk.adapters.pydantic_ai.prompt import build_user_prompt
    from tests.support.harness import make_runner
    from tests.support.runs import EchoInput, EchoResult, run_body

    retained = _Document("start " * 10000 + "HIDDEN_MARKER")
    configuration = StepExecutorConfiguration(
        context_limits=StepContextLimits(value_max_tokens=40, locals_max_tokens=200, object_field_value_max_tokens=40)
    )
    prompts: list[str] = []

    @nh.function
    def inspect_document(document: _Document) -> str:
        result: str = ""
        """execute
        Read <document> and set <:result>.
        """
        return result

    class RenderingExecutor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            processed_natural_program = request.processed_natural_program
            step_context = request.step_context
            assert step_context.step_locals["document"] is retained
            prompt = build_user_prompt(processed_natural_program=processed_natural_program, step_context=step_context, configuration=configuration)
            prompts.append("".join(part for part in prompt if isinstance(part, str)))
            marker = read_text_window(retained.content, starting_character=60000).text
            return StepCandidate(outcome=Pass(), binding_name_to_value={"result": marker})

    def body(run_input: EchoInput) -> EchoResult:
        return EchoResult(value=inspect_document(retained))

    result = run_body(body, runner=make_runner(), step_executor=RenderingExecutor())
    assert result.value == "HIDDEN_MARKER"
    assert "HIDDEN_MARKER" not in prompts[0] and len(prompts[0]) < 3000
    assert retained.content.endswith("HIDDEN_MARKER")
