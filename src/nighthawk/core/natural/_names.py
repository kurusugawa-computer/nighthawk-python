"""Allocate instrumentation names without taking application bindings."""


class _RuntimeNames:
    def __init__(self, occupied_names: set[str] | None = None) -> None:
        self._occupied_names = set(occupied_names or ())
        self._original_name_to_generated_name: dict[str, str] = {}

    def allocate(self, name: str) -> str:
        if name in self._original_name_to_generated_name:
            return self._original_name_to_generated_name[name]
        generated_name = name
        suffix = 1
        while generated_name in self._occupied_names:
            generated_name = f"{name}{suffix}"
            suffix += 1
        self._occupied_names.add(generated_name)
        self._original_name_to_generated_name[name] = generated_name
        return generated_name

    @property
    def allocated_names(self) -> frozenset[str]:
        return frozenset(self._original_name_to_generated_name.values())
