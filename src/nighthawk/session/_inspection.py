"""Pure bounded projection of built-in values; arbitrary repr and properties stay uncalled."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from nighthawk.harness.proposals import CodeProposal, DependencyDeclaration
from nighthawk.parts.sources import SourceCandidate
from nighthawk.parts.values import BasicDataValue


@dataclass
class _ProjectionBudget:
    characters: int
    entries: int = 200
    incomplete: bool = False

    def text(self, value: str) -> str:
        selected = value[: self.characters]
        self.characters -= len(selected)
        self.incomplete |= len(selected) != len(value)
        return selected

    def value(self, value: object, depth: int = 0, ancestors: frozenset[int] = frozenset()) -> BasicDataValue:
        if self.entries == 0 or self.characters == 0:
            self.incomplete = True
            return None
        self.entries -= 1
        if depth > 4:
            self.incomplete = True
            return None
        if type(value) is SourceCandidate:
            proposal = value.proposal
            if type(proposal) is not CodeProposal or type(proposal.dependency_tuple) is not tuple:
                self.incomplete = True
                return None
            dependencies = proposal.dependency_tuple[: self.entries]
            self.incomplete |= len(dependencies) < len(proposal.dependency_tuple)
            return self.value(
                {
                    "source": proposal.source,
                    "export_name_tuple": proposal.export_name_tuple,
                    "dependency_name_tuple": tuple(item.name if type(item) is DependencyDeclaration else None for item in dependencies),
                    "originating_run_id": value.originating_run_id,
                    "originating_cell_id": value.originating_cell_id,
                },
                depth,
                ancestors,
            )
        value_type = type(value)
        if value_type is str:
            return self.text(cast(str, value))
        if any(value_type is basic_type for basic_type in (type(None), bool, int, float)):
            if value_type is int and cast(int, value).bit_length() > 10000:
                self.incomplete = True
                return None
            display = str(value)
            if len(display) > self.characters:
                self.incomplete = True
                return None
            self.text(display)
            return cast(BasicDataValue, value)
        if not any(value_type is collection_type for collection_type in (list, tuple, dict)) or id(value) in ancestors:
            self.incomplete = True
            return None
        ancestors = ancestors | {id(value)}
        if value_type is dict:
            key_to_value: dict[str, BasicDataValue] = {}
            for key, item in cast(dict[object, object], value).items():
                if not self.entries or not self.characters:
                    self.incomplete = True
                    break
                if type(key) is not str:
                    self.entries -= 1
                    self.incomplete = True
                    continue
                if len(cast(str, key)) > self.characters:
                    self.incomplete = True
                    break
                key_to_value[self.text(cast(str, key))] = self.value(item, depth + 1, ancestors)
            return key_to_value
        values: list[BasicDataValue] = []
        for item in cast(list[object] | tuple[object, ...], value):
            if not self.entries or not self.characters:
                self.incomplete = True
                break
            values.append(self.value(item, depth + 1, ancestors))
        return tuple(values) if value_type is tuple else values
