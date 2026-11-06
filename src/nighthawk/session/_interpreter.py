"""Compile cells with persistent future flags, owned source, and one expression evaluation."""

from __future__ import annotations
import __future__

import ast
import inspect
import linecache
import sys
from collections.abc import Callable
from typing import Any

from nighthawk.session._source import _SourceRegistry

_FUTURE_FLAGS = sum(getattr(__future__, name).compiler_flag for name in __future__.all_feature_names)


class _Interpreter:
    def __init__(self, session_id: str, binding_name_to_value: dict[str, Any], sources: _SourceRegistry) -> None:
        self.session_id = session_id
        self.binding_name_to_value = binding_name_to_value
        self.sources = sources
        self.future_flags = 0
        self.filename_list: list[str] = []

    async def execute(self, source: str, cell_id: str, on_result: Callable[[object], None]) -> None:
        filename = f"<nighthawk-session-{self.session_id}-cell-{cell_id}>"
        self.filename_list.append(filename)
        linecache.cache[filename] = (len(source), None, source.splitlines(keepends=True), filename)
        module = compile(source, filename, "exec", flags=self.future_flags | ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT, dont_inherit=True)
        assert isinstance(module, ast.Module)
        helper_names = self.sources.instrument(module, source, cell_id, self.binding_name_to_value)
        result_name = f"__nighthawk_result_{cell_id}"
        self.binding_name_to_value[result_name] = on_result
        previous_annotate = self.binding_name_to_value.get("__annotate__")
        try:
            if module.body and isinstance(module.body[-1], ast.Expr):
                expression = module.body[-1]
                module.body[-1] = ast.copy_location(
                    ast.Expr(value=ast.Call(func=ast.Name(id=result_name, ctx=ast.Load()), args=[expression.value], keywords=[])),
                    expression,
                )
            ast.fix_missing_locations(module)
            code = compile(module, filename, "exec", flags=self.future_flags | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT, dont_inherit=True)
            self.future_flags |= code.co_flags & _FUTURE_FLAGS
            result = eval(code, self.binding_name_to_value)
            if code.co_flags & inspect.CO_COROUTINE:
                await result
        finally:
            for name in (*helper_names, result_name):
                self.binding_name_to_value.pop(name, None)
            if sys.version_info >= (3, 14):
                import annotationlib

                annotate = self.binding_name_to_value.get("__annotate__")
                if callable(annotate) and annotate is not previous_annotate:
                    # Each cell replaces the deferred annotator; retain its declarations without evaluating unselected contracts.
                    annotations = annotationlib.call_annotate_function(annotate, annotationlib.Format.STRING)
                    self.binding_name_to_value.setdefault("__annotations__", {}).update(annotations)

    def dispose(self) -> None:
        for filename in self.filename_list:
            linecache.cache.pop(filename, None)
        self.filename_list.clear()
        self.binding_name_to_value = {}
