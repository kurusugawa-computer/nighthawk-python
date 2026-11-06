"""Explicit text persistence through ordinary host-mediated storage capabilities.

Descriptors locate saved data. They are neither live-object references nor
capability authority. Publication and durable audit commits are separate effects.
"""

from collections.abc import Iterable
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from .._grants import Capability, Grant
from .._perimeter import EffectFootprint

__all__ = [
    "ArtifactStore",
    "DirectoryArtifactStore",
    "InMemoryArtifactStore",
    "LoadArtifactRequest",
    "SaveArtifactRequest",
    "StoredArtifactDescriptor",
    "build_artifact_storage",
]


class StoredArtifactDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    store_id: str
    artifact_id: str
    media_type: str
    character_count: int = Field(ge=0)


class SaveArtifactRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    media_type: str


class LoadArtifactRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    descriptor: StoredArtifactDescriptor


class ArtifactStore(Protocol):
    @property
    def identity(self) -> str: ...

    @property
    def write_effect_footprint(self) -> EffectFootprint: ...

    def put_text(self, chunks: Iterable[str], *, media_type: str) -> StoredArtifactDescriptor: ...

    def load_text(self, descriptor: StoredArtifactDescriptor, *, maximum_character_count: int) -> str: ...


def build_artifact_storage(*, store: ArtifactStore, maximum_payload_character_count: int, allow_write: bool = False) -> Grant:
    """Bind destination, complete-payload bounds, and effects before run entry.

    Oversized loads fail instead of returning a truncated handoff. The ordinary
    host records durable attempts before invoking save and observes completion;
    this adapter does not coordinate artifact publication with the audit store.
    """
    if maximum_payload_character_count <= 0:
        raise ValueError("maximum_payload_character_count must be positive")

    def load_artifact(request: LoadArtifactRequest) -> str:
        """Load one complete saved text within the configured payload limit."""
        return store.load_text(request.descriptor, maximum_character_count=maximum_payload_character_count)

    def save_artifact(request: SaveArtifactRequest) -> StoredArtifactDescriptor:
        """Publish explicitly encoded text; return only a complete artifact locator."""
        if len(request.text) > maximum_payload_character_count:
            raise ValueError("Saved text exceeds the configured complete-payload limit")
        return store.put_text((request.text,), media_type=request.media_type)

    capabilities = [
        Capability(name="load_artifact", function=load_artifact, authority_boundary=f"complete text from configured store {store.identity}")
    ]
    if allow_write:
        capabilities.append(
            Capability(
                name="save_artifact",
                function=save_artifact,
                authority_boundary=f"explicit text publication to configured store {store.identity}",
                effect_footprint=store.write_effect_footprint,
            )
        )
    return Grant(capabilities)


# Backends depend on the descriptor above, so expose them after its definition.
from ._artifact_backends import DirectoryArtifactStore, InMemoryArtifactStore  # noqa: E402
