"""Explicit values shared by conversation parts and their host integrations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal

from pydantic import ConfigDict

from nighthawk.harness.evidence import EvidenceReference
from nighthawk.harness.proposals import CodeProposal
from nighthawk.parts.generation import FunctionRequest
from nighthawk.parts.values import BasicDataValue


class _TypedResult:
    # The run boundary must validate even a Python-constructed dataclass instance.
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(revalidate_instances="always", extra="forbid")


@dataclass(frozen=True)
class ConversationRecord:
    record_index: int
    kind: str
    text: str
    run_id: str | None = None
    event_id: str | None = None
    incomplete: bool = False


@dataclass(frozen=True)
class ConversationPage:
    record_tuple: tuple[ConversationRecord, ...]
    horizon: int
    next_starting_index: int | None
    incomplete: bool


@dataclass(frozen=True)
class ContextItem:
    name: str
    type_name: str
    value: BasicDataValue = None
    text: str = ""
    source_identity: str | None = None
    incomplete: bool = False


@dataclass(frozen=True)
class ContextPage:
    item_tuple: tuple[ContextItem, ...]
    horizon: int | None
    next_starting_index: int | None
    incomplete: bool


@dataclass(frozen=True)
class ContextQuery:
    starting_index: int = 0
    maximum_item_count: int = 20
    maximum_character_count: int = 6000


@dataclass(frozen=True)
class FunctionSelection:
    binding_name: str


@dataclass(frozen=True)
class ConversationHistoryQuery:
    query: str | None = None
    starting_index: int = 0
    horizon: int | None = None
    maximum_item_count: int = 20
    maximum_character_count: int = 6000


@dataclass(frozen=True)
class ConversationInput:
    task: str
    namespace_page: ContextPage = ContextPage((), None, None, False)
    function_page: ContextPage = ContextPage((), None, None, False)
    recent_history: ConversationPage = ConversationPage((), 0, None, False)
    context: object = None


@dataclass(frozen=True)
class UserFacingBlock(_TypedResult):
    kind: Literal["answer", "proposed_source", "result", "failure"]
    text: str
    evidence_reference_tuple: tuple[EvidenceReference, ...] = ()


@dataclass(frozen=True)
class FunctionDeclaration(_TypedResult):
    """Materialize a definition-only proposal and return a retained callable for explicit assignment."""

    proposal: CodeProposal
    export_name: str


@dataclass(frozen=True)
class SourceRetention(_TypedResult):
    """Request explicit retention of inert source; the caller supplies origin metadata."""

    binding_name: str
    proposal: CodeProposal


@dataclass(frozen=True)
class SourceInvocation(_TypedResult):
    """Rematerialize a selected candidate and invoke a fresh export in this run.

    For a combined proposal, omit export_name and arguments to use its declared entry.
    Definition-only proposals require an explicit export name and call arguments.
    """

    candidate_name: str
    export_name: str | None = None
    argument_tuple: tuple[BasicDataValue, ...] = ()
    keyword_argument_name_to_value: dict[str, BasicDataValue] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceExport(_TypedResult):
    """Export selected exact source and a dependency/provenance JSON sidecar."""

    candidate_name: str
    file_path: str


@dataclass(frozen=True)
class DataExport(_TypedResult):
    """Export one explicitly selected JSON-compatible reference through the writer."""

    reference_name: str
    file_path: str


@dataclass(frozen=True)
class ConversationResult(_TypedResult):
    block_tuple: tuple[UserFacingBlock, ...]
    binding_name_to_value: dict[str, object] = field(default_factory=dict)
    source_retention_tuple: tuple[SourceRetention, ...] = ()


type ConversationAction = FunctionRequest | FunctionDeclaration | SourceInvocation | SourceExport | DataExport


@dataclass(frozen=True)
class ConversationBindings:
    """Explicit binding contract and values selected by the composition's owner.

    Each judgment returns its
    selected writes; the caller decides when and where to publish them.
    """

    read_name_tuple: tuple[str, ...] = ()
    write_name_tuple: tuple[str, ...] = ()
    binding_name_to_type: dict[str, object] = field(default_factory=dict)
    binding_name_to_value: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ConversationStep:
    """A selected action or final response, plus this judgment's explicit writes."""

    result: ConversationResult | ConversationAction
    binding_name_to_value: dict[str, object] = field(default_factory=dict)
