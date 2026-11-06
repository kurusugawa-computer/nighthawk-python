"""Finite optional artifact adapters; no live-object registry or recovery service."""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path

from nighthawk.harness.perimeter import EffectFootprint
from nighthawk.parts.artifacts import StoredArtifactDescriptor

__all__ = ["DirectoryArtifactStore", "InMemoryArtifactStore"]


def _collect(chunks: Iterable[str], *, maximum_character_count: int) -> str:
    chunk_list = []
    count = 0
    for chunk in chunks:
        count += len(chunk)
        if count > maximum_character_count:
            raise ValueError("Artifact store capacity exceeded")
        chunk_list.append(chunk)
    return "".join(chunk_list)


def _check_descriptor(descriptor: StoredArtifactDescriptor, *, store_id: str, maximum_character_count: int) -> None:
    if maximum_character_count <= 0:
        raise ValueError("maximum_character_count must be positive")
    if descriptor.store_id != store_id:
        raise ValueError("Descriptor belongs to another store")
    if descriptor.character_count > maximum_character_count:
        raise ValueError("Artifact exceeds the complete-payload limit")
    if not re.fullmatch("[0-9a-f]{32}", descriptor.artifact_id):
        raise ValueError("Invalid artifact identity")


class InMemoryArtifactStore:
    """Finite process-local saved text, with no durability claim."""

    def __init__(self, *, maximum_character_count: int = 1_000_000) -> None:
        if maximum_character_count <= 0:
            raise ValueError("Store capacity must be positive")
        self._identity = secrets.token_hex(16)
        self.maximum_character_count = maximum_character_count
        self._artifact_id_to_record: dict[str, tuple[StoredArtifactDescriptor, str]] = {}
        self._lock = threading.Lock()

    @property
    def identity(self) -> str:
        return self._identity

    @property
    def write_effect_footprint(self) -> EffectFootprint:
        return EffectFootprint()

    def put_text(self, chunks: Iterable[str], *, media_type: str) -> StoredArtifactDescriptor:
        with self._lock:
            remaining = self.maximum_character_count - sum(len(text) for _, text in self._artifact_id_to_record.values())
            text = _collect(chunks, maximum_character_count=remaining)
            descriptor = StoredArtifactDescriptor(
                store_id=self.identity, artifact_id=secrets.token_hex(16), media_type=media_type, character_count=len(text)
            )
            self._artifact_id_to_record[descriptor.artifact_id] = (descriptor, text)
            return descriptor

    def load_text(self, descriptor: StoredArtifactDescriptor, *, maximum_character_count: int) -> str:
        _check_descriptor(descriptor, store_id=self.identity, maximum_character_count=maximum_character_count)
        with self._lock:
            stored, text = self._artifact_id_to_record[descriptor.artifact_id]
        if stored != descriptor:
            raise ValueError("Descriptor does not match the saved artifact")
        return text


class DirectoryArtifactStore:
    """Atomically publish immutable artifacts readable after process restart.

    Completed files can be reopened while the same filesystem is available.
    Atomic visibility makes no power-loss durability promise. Capacity admission
    is serialized within this adapter; callers coordinate independent writers to
    the same destination. Constructing an adapter initializes its destination
    and persistent store identity as an explicit caller-side configuration effect.
    """

    def __init__(self, directory: Path, *, maximum_character_count: int = 1_000_000) -> None:
        if maximum_character_count <= 0:
            raise ValueError("Store capacity must be positive")
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.maximum_character_count = maximum_character_count
        self._lock = threading.Lock()
        identity_path = self.directory / "store-identity"
        if not identity_path.exists():
            with suppress(FileExistsError):
                self._publish(identity_path, secrets.token_hex(16))
        self._identity = identity_path.read_text(encoding="utf-8")
        if not re.fullmatch("[0-9a-f]{32}", self._identity):
            raise ValueError("Unreadable store identity")

    @property
    def identity(self) -> str:
        return self._identity

    @property
    def write_effect_footprint(self) -> EffectFootprint:
        return EffectFootprint(perimeter_effect_class_set=frozenset({"durable_write_back"}))

    def _publish(self, destination: Path, text: str) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory, prefix=".staging-", delete=False) as stream:
                temporary_path = Path(stream.name)
                stream.write(text)
            os.link(temporary_path, destination)
        finally:
            if temporary_path is not None:
                # Do not mask publication failure or invalidate a completed artifact.
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)

    def put_text(self, chunks: Iterable[str], *, media_type: str) -> StoredArtifactDescriptor:
        with self._lock:
            occupied = sum(len(json.loads(path.read_text(encoding="utf-8"))["text"]) for path in self.directory.glob("*.artifact"))
            text = _collect(chunks, maximum_character_count=self.maximum_character_count - occupied)
            descriptor = StoredArtifactDescriptor(
                store_id=self.identity, artifact_id=secrets.token_hex(16), media_type=media_type, character_count=len(text)
            )
            payload = json.dumps({"descriptor": descriptor.model_dump(), "text": text}, ensure_ascii=False)
            self._publish(self.directory / f"{descriptor.artifact_id}.artifact", payload)
            return descriptor

    def load_text(self, descriptor: StoredArtifactDescriptor, *, maximum_character_count: int) -> str:
        _check_descriptor(descriptor, store_id=self.identity, maximum_character_count=maximum_character_count)
        payload = json.loads((self.directory / f"{descriptor.artifact_id}.artifact").read_text(encoding="utf-8"))
        saved = StoredArtifactDescriptor.model_validate(payload["descriptor"])
        text = payload["text"]
        if saved != descriptor or not isinstance(text, str) or len(text) != descriptor.character_count:
            raise ValueError("Descriptor does not match a complete saved artifact")
        return text
