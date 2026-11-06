"""Typed forms preserve the complete source-owned query arguments."""

from examples.repl.operations import operation_fields, typed_request
from nighthawk.agent.history import HistoryBounds, HistoryQuery
from nighthawk.agent.session import ReadEvidenceRequest, SearchHistoryRequest, create_session


def test_evidence_form_roundtrips_all_bounds_and_filters_without_source_evaluation() -> None:
    session = create_session(execution_model="openai-responses:gpt-6-luna")
    try:
        descriptor = session.help("session.read_run_evidence")[0]
        fields = operation_fields(descriptor)
        request = typed_request(descriptor.name, fields, ("run", "4", "dispatch,run_end", "node", "3", "500", "100"))
        assert request == ReadEvidenceRequest(
            "run",
            HistoryQuery(starting_sequence=4, event_kind_tuple=("dispatch", "run_end"), attribution_node_id="node"),
            HistoryBounds(maximum_event_count=3, maximum_character_count=500, maximum_excerpt_character_count=100),
        )
        descriptor = session.help("session.history.search")[0]
        fields = operation_fields(descriptor)
        values = tuple("literal __import__('os')" if field.path == ("query",) else field.default for field in fields)
        request = typed_request(descriptor.name, fields, values)
        assert isinstance(request, SearchHistoryRequest) and request.query == "literal __import__('os')"
    finally:
        session.close()
