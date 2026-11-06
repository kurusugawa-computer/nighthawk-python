"""Code proposals and deterministic validation of generated source.

A code proposal is an inert, immutable typed value carrying Python source, an
execution intent, declared exports and dependencies, and a declared effect
footprint. Creating a proposal performs no execution effect. Source validation
here is deterministic and detective: it rejects raw dynamic execution and
materialization effects where they are detectable from the source, including
calls hidden in default argument values, annotations (also string annotations
the host resolves later), comprehensions, and formatted strings evaluated while
the module body defines its exports. Validated source is then instrumented with
the host execution guard so that generated code can start or resume running only
inside a live generated execution of its own dispatch.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import symtable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ._call_contracts import build_value_adapter
from ._execution_guards import GUARD_NAME, WRAP_ASYNC_GENERATOR_NAME, WRAP_COROUTINE_NAME, WRAP_GENERATOR_NAME
from ._grants import GrantEntryKind, validate_identifier
from ._ledger import DefinitionKind
from ._perimeter import EffectFootprint
from .errors import DispatchRejectionReason

__all__ = [
    "CodeProposal",
    "DependencyDeclaration",
    "ExecutionIntent",
    "detect_raw_dynamic_execution",
]


RESERVED_NAME_PREFIX = "__nighthawk"
_HIDDEN_BODY_NAME_PREFIX = "__nighthawk_body_"


type ExecutionIntent = Literal["definition_only", "definition_and_invocation"]


class DependencyDeclaration(BaseModel):
    """One nonlocal dependency of generated source, resolved from the grant by name and declared type.

    ``declared_type`` is the type the generated source was written against. For
    a ``capability`` dependency it is the capability's declared call signature
    text exactly as projected into the prompt (for example ``"(name: str) -> str"``);
    the host rejects the dispatch when the granted capability's signature
    differs. For a ``reference`` dependency it is a Python type expression the
    granted reference must satisfy under strict validation (for example ``str``
    or ``list[int]``); the object itself is bound, never a coerced copy.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    name: str
    kind: GrantEntryKind
    declared_type: object

    @field_validator("name")
    @classmethod
    def _validate_name(cls, name: str) -> str:
        validate_identifier(name, role="DependencyDeclaration.name")
        return name

    @model_validator(mode="after")
    def _validate_declared_type(self) -> DependencyDeclaration:
        if self.kind == "capability":
            if not isinstance(self.declared_type, str) or not self.declared_type.strip():
                raise ValueError("a capability dependency declares its type as the capability's signature text")
            return self
        if isinstance(self.declared_type, str):
            raise ValueError("a reference dependency declares its type as a Python type expression, not text")  # noqa: TRY004 - Pydantic validators report invalid declarations as validation errors.
        try:
            build_value_adapter(self.declared_type)
        except Exception as error:
            raise ValueError("a reference dependency must declare a validatable Python type") from error
        return self


class CodeProposal(BaseModel):
    """Inert typed proposal of Python source for host-owned dispatch.

    ``execution_intent`` is exactly one of ``definition_only`` (materialize the
    declared exports without invoking them) or ``definition_and_invocation``
    (materialize, then invoke ``entry_export_name`` with the entry arguments
    under its call contract).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    source: str = Field(min_length=1)
    execution_intent: ExecutionIntent = "definition_only"
    export_name_tuple: tuple[str, ...] = Field(min_length=1)
    dependency_tuple: tuple[DependencyDeclaration, ...] = ()
    effect_footprint: EffectFootprint = EffectFootprint()
    entry_export_name: str | None = None
    entry_argument_tuple: tuple[object, ...] = ()
    entry_keyword_argument_name_to_value: dict[str, object] = Field(default_factory=dict)

    @field_validator("export_name_tuple")
    @classmethod
    def _validate_export_names(cls, export_name_tuple: tuple[str, ...]) -> tuple[str, ...]:
        for export_name in export_name_tuple:
            validate_identifier(export_name, role="CodeProposal export name")
            if export_name.startswith("_"):
                raise ValueError(f"export name {export_name!r} must not start with an underscore")
        if len(set(export_name_tuple)) != len(export_name_tuple):
            raise ValueError("export names must be unique")
        return export_name_tuple

    @field_validator("dependency_tuple")
    @classmethod
    def _validate_dependencies(cls, dependency_tuple: tuple[DependencyDeclaration, ...]) -> tuple[DependencyDeclaration, ...]:
        name_list = [dependency.name for dependency in dependency_tuple]
        if len(set(name_list)) != len(name_list):
            raise ValueError("dependency names must be unique")
        return dependency_tuple

    @model_validator(mode="after")
    def _validate_intent(self) -> CodeProposal:
        if self.execution_intent == "definition_only":
            if self.entry_export_name is not None or self.entry_argument_tuple or self.entry_keyword_argument_name_to_value:
                raise ValueError("definition_only proposals carry no entry export or entry arguments")
            return self
        if self.entry_export_name is None:
            raise ValueError("definition_and_invocation proposals must name entry_export_name")
        if self.entry_export_name not in self.export_name_tuple:
            raise ValueError("entry_export_name must be one of the declared exports")
        return self

    @property
    def dependency_name_tuple(self) -> tuple[str, ...]:
        return tuple(dependency.name for dependency in self.dependency_tuple)

    @property
    def defining_source_identity(self) -> str:
        """Content identity of the source text (SHA-256, hexadecimal)."""

        return hashlib.sha256(self.source.encode("utf-8")).hexdigest()


class SourceRejectedError(Exception):
    """Internal: source validation failed for a deterministic reason."""

    def __init__(self, reason: DispatchRejectionReason, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason: DispatchRejectionReason = reason
        self.detail = detail


@dataclass(frozen=True)
class SourceAnalysis:
    """Deterministic facts about validated generated source.

    ``instrumented_source`` is the source text the host compiles: the validated
    source with the execution guard inserted into every function body, lambda,
    generator expression, and resumption point (see
    :func:`instrument_generated_source`). ``export_name_to_docstring`` keeps the
    docstrings the guard displaces from the first statement position.
    """

    export_name_to_kind: dict[str, DefinitionKind]
    free_name_tuple: tuple[str, ...]
    global_write_name_tuple: tuple[str, ...]
    instrumented_source: str
    export_name_to_docstring: dict[str, str | None]


_RAW_DYNAMIC_EXECUTION_NAME_SET = frozenset({"exec", "eval", "compile", "__import__"})
_RAW_DYNAMIC_EXECUTION_ATTRIBUTE_SET = frozenset(
    {"exec", "eval", "compile", "import_module", "run_path", "run_module", "load_module", "exec_module", "reload"}
)
_INTROSPECTION_NAME_SET = frozenset({"globals", "locals", "vars"})
_INTROSPECTION_ATTRIBUTE_SET = frozenset(
    {"__globals__", "__code__", "__closure__", "__builtins__", "f_globals", "f_locals", "f_back", "gi_frame", "cr_frame"}
)
_MAXIMUM_STRING_ANNOTATION_DEPTH = 4
_MODULE_DUNDER_NAME_SET = frozenset({"__name__", "__doc__", "__file__", "__builtins__", "__spec__", "__loader__", "__package__"})
_BUILTIN_NAME_SET = frozenset(dir(builtins))
_CLASS_BODY_DECORATOR_NAME_SET = frozenset({"staticmethod", "classmethod", "property"})


def _is_nighthawk_function_decorator(decorator: ast.expr) -> bool:
    return (
        isinstance(decorator, ast.Attribute)
        and isinstance(decorator.value, ast.Name)
        and decorator.attr == "function"
        and decorator.value.id in {"nh", "nighthawk"}
    )


_EFFECTFUL_EXPRESSION_TYPE_TUPLE = (
    ast.Call,
    ast.Await,
    ast.Yield,
    ast.YieldFrom,
    ast.NamedExpr,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.JoinedStr,
)


def _contains_effectful_expression(node: ast.AST) -> bool:
    """Whether evaluating ``node`` at materialization time could perform an effect beyond defining a name."""

    return any(isinstance(child, _EFFECTFUL_EXPRESSION_TYPE_TUPLE) for child in ast.walk(node))


def _contains_yield(function_definition: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> bool:
    """Whether the function's own body (not nested functions) contains ``yield`` or ``yield from``."""

    body_nodes: list[ast.AST] = [function_definition.body] if isinstance(function_definition, ast.Lambda) else list(function_definition.body)
    stack = list(body_nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Yield | ast.YieldFrom):
            return True
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return False


def _validate_function_definition_evaluates_nothing(function_definition: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
    """Reject default values and annotations whose evaluation at definition time would perform an effect."""

    arguments = function_definition.args
    for default in (*arguments.defaults, *(default for default in arguments.kw_defaults if default is not None)):
        if _contains_effectful_expression(default):
            raise SourceRejectedError("materialization_effect", f"function {function_definition.name} evaluates a default argument expression")
    annotation_list: list[ast.expr] = [
        argument.annotation for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs) if argument.annotation is not None
    ]
    if arguments.vararg is not None and arguments.vararg.annotation is not None:
        annotation_list.append(arguments.vararg.annotation)
    if arguments.kwarg is not None and arguments.kwarg.annotation is not None:
        annotation_list.append(arguments.kwarg.annotation)
    if function_definition.returns is not None:
        annotation_list.append(function_definition.returns)
    for annotation in annotation_list:
        if _annotation_evaluates_something(annotation):
            raise SourceRejectedError("materialization_effect", f"function {function_definition.name} evaluates an annotation expression")


def detect_raw_dynamic_execution(tree: ast.AST) -> str | None:
    """Return a description of the first raw dynamic execution in ``tree``, or ``None``.

    The rule of I-021: calls to ``exec``, ``eval``, ``compile``, ``__import__``,
    and the import-machinery attributes that execute constructed text. It is
    shared by generated-source validation and by the inspection of
    model-authored expressions the executor evaluates.
    """

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name) and function.id in _RAW_DYNAMIC_EXECUTION_NAME_SET:
            return f"call to {function.id}"
        if isinstance(function, ast.Attribute) and function.attr in _RAW_DYNAMIC_EXECUTION_ATTRIBUTE_SET:
            return f"call to attribute {function.attr}"
    return None


def _validate_no_raw_dynamic_execution(module: ast.Module) -> None:
    """Reject raw dynamic execution, reserved host names, and the introspection that could reach or rebind them."""

    dynamic_execution = detect_raw_dynamic_execution(module)
    if dynamic_execution is not None:
        raise SourceRejectedError("raw_dynamic_execution", dynamic_execution)
    for node in ast.walk(module):
        if isinstance(node, ast.Name) and node.id.startswith(RESERVED_NAME_PREFIX):
            raise SourceRejectedError("reserved_name", node.id)
        if isinstance(node, ast.Attribute):
            if node.attr.startswith(RESERVED_NAME_PREFIX):
                raise SourceRejectedError("reserved_name", node.attr)
            if node.attr in _INTROSPECTION_ATTRIBUTE_SET:
                raise SourceRejectedError("introspection_not_allowed", f"attribute {node.attr}")
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and node.name.startswith(RESERVED_NAME_PREFIX):
            raise SourceRejectedError("reserved_name", node.name)
        if isinstance(node, ast.arg) and node.arg.startswith(RESERVED_NAME_PREFIX):
            raise SourceRejectedError("reserved_name", node.arg)
        if isinstance(node, ast.alias) and (node.asname or node.name).startswith(RESERVED_NAME_PREFIX):
            raise SourceRejectedError("reserved_name", node.asname or node.name)
        if isinstance(node, ast.Lambda) and _contains_yield(node):
            raise SourceRejectedError("unsupported_syntax", "a lambda may not be a generator")
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name) and function.id in _INTROSPECTION_NAME_SET:
            raise SourceRejectedError("introspection_not_allowed", f"call to {function.id}")


def _annotation_evaluates_something(annotation: ast.expr, *, depth: int = 0) -> bool:
    """Whether evaluating ``annotation`` (including a string annotation the host later resolves) could perform an effect."""

    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        if depth >= _MAXIMUM_STRING_ANNOTATION_DEPTH:
            return True
        try:
            parsed = ast.parse(annotation.value, mode="eval")
        except SyntaxError:
            return True
        return _annotation_evaluates_something(parsed.body, depth=depth + 1)
    for child in ast.walk(annotation):
        if isinstance(child, _EFFECTFUL_EXPRESSION_TYPE_TUPLE):
            return True
        if (
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and child is not annotation
            and _annotation_evaluates_something(child, depth=depth + 1)
        ):
            return True
    return False


def _validate_class_body(class_definition: ast.ClassDef) -> None:
    if class_definition.decorator_list:
        raise SourceRejectedError("decorator_not_allowed", f"class {class_definition.name} carries decorators")
    if class_definition.keywords:
        raise SourceRejectedError("materialization_effect", f"class {class_definition.name} declares keywords")
    for base in class_definition.bases:
        if _contains_effectful_expression(base):
            raise SourceRejectedError("materialization_effect", f"class {class_definition.name} has an effectful base expression")
    for statement in class_definition.body:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            _validate_function_definition_evaluates_nothing(statement)
            if isinstance(statement, ast.AsyncFunctionDef) or _contains_yield(statement):
                raise SourceRejectedError(
                    "unsupported_syntax", f"method {statement.name} is a generator or coroutine; define it as a module-level function"
                )
            for decorator in statement.decorator_list:
                if _is_nighthawk_function_decorator(decorator):
                    continue
                if isinstance(decorator, ast.Name) and decorator.id in _CLASS_BODY_DECORATOR_NAME_SET:
                    continue
                if isinstance(decorator, ast.Attribute) and decorator.attr in {"setter", "getter", "deleter"}:
                    continue
                raise SourceRejectedError("decorator_not_allowed", f"method {statement.name} carries an unsupported decorator")
            continue
        if isinstance(statement, ast.Assign | ast.AnnAssign):
            if statement.value is not None and _contains_effectful_expression(statement.value):
                raise SourceRejectedError("materialization_effect", f"class {class_definition.name} body evaluates a call")
            if isinstance(statement, ast.AnnAssign) and _annotation_evaluates_something(statement.annotation):
                raise SourceRejectedError("materialization_effect", f"class {class_definition.name} body evaluates an annotation expression")
            continue
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
            continue
        if isinstance(statement, ast.Pass):
            continue
        raise SourceRejectedError("materialization_effect", f"class {class_definition.name} body contains {type(statement).__name__}")


def _assign_target_names(statement: ast.Assign | ast.AnnAssign) -> tuple[str, ...]:
    if isinstance(statement, ast.AnnAssign):
        if not isinstance(statement.target, ast.Name):
            raise SourceRejectedError("materialization_effect", "annotated assignment to a non-name target")
        return (statement.target.id,)
    names: list[str] = []
    for target in statement.targets:
        if not isinstance(target, ast.Name):
            raise SourceRejectedError("materialization_effect", "assignment to a non-name target")
        names.append(target.id)
    return tuple(names)


def _import_binding_names(statement: ast.Import | ast.ImportFrom) -> tuple[str, ...]:
    names: list[str] = []
    for alias in statement.names:
        if alias.asname is not None:
            names.append(alias.asname)
        elif alias.name == "*":
            raise SourceRejectedError("materialization_effect", "star imports are not allowed")
        else:
            names.append(alias.name.split(".", 1)[0])
    return tuple(names)


def _collect_module_definitions(
    module: ast.Module,
) -> tuple[dict[str, DefinitionKind | Literal["class"]], set[str]]:
    defined_name_to_kind: dict[str, DefinitionKind | Literal["class"]] = {}
    imported_name_set: set[str] = set()
    for statement in module.body:
        if isinstance(statement, ast.FunctionDef):
            _validate_function_definition_evaluates_nothing(statement)
            if not all(_is_nighthawk_function_decorator(decorator) for decorator in statement.decorator_list):
                raise SourceRejectedError("decorator_not_allowed", f"function {statement.name} carries an unsupported decorator")
            defined_name_to_kind[statement.name] = "function"
            continue
        if isinstance(statement, ast.AsyncFunctionDef):
            _validate_function_definition_evaluates_nothing(statement)
            if not all(_is_nighthawk_function_decorator(decorator) for decorator in statement.decorator_list):
                raise SourceRejectedError("decorator_not_allowed", f"function {statement.name} carries an unsupported decorator")
            defined_name_to_kind[statement.name] = "async_function"
            continue
        if isinstance(statement, ast.ClassDef):
            _validate_class_body(statement)
            defined_name_to_kind[statement.name] = "class"
            continue
        if isinstance(statement, ast.Import | ast.ImportFrom):
            imported_name_set.update(_import_binding_names(statement))
            continue
        if isinstance(statement, ast.Assign | ast.AnnAssign):
            if statement.value is not None and _contains_effectful_expression(statement.value):
                raise SourceRejectedError("materialization_effect", "module-level assignment evaluates a call")
            if isinstance(statement, ast.AnnAssign) and _annotation_evaluates_something(statement.annotation):
                raise SourceRejectedError("materialization_effect", "module-level annotation evaluates a call")
            for name in _assign_target_names(statement):
                defined_name_to_kind[name] = "value"
            continue
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
            continue
        if isinstance(statement, ast.Pass):
            continue
        raise SourceRejectedError("materialization_effect", f"module-level {type(statement).__name__} statement")
    return defined_name_to_kind, imported_name_set


def _collect_free_and_written_globals(source: str, filename: str) -> tuple[set[str], set[str]]:
    try:
        module_table = symtable.symtable(source, filename, "exec")
    except SyntaxError as error:
        raise SourceRejectedError("source_syntax_error", str(error)) from error
    source_name_set = {node.id for node in ast.walk(ast.parse(source, filename=filename)) if isinstance(node, ast.Name)}
    module_defined_name_set = {symbol.get_name() for symbol in module_table.get_symbols() if symbol.is_assigned() or symbol.is_imported()}
    referenced_global_name_set: set[str] = set()
    written_global_name_set: set[str] = set()

    def visit(table: symtable.SymbolTable) -> None:
        is_module = table.get_type() == "module"
        for symbol in table.get_symbols():
            name = symbol.get_name()
            # Python 3.14 adds this reference for deferred module annotations.
            if name == "__conditional_annotations__" and name not in source_name_set:
                continue
            if is_module:
                if symbol.is_referenced() and not (symbol.is_assigned() or symbol.is_imported()):
                    referenced_global_name_set.add(name)
                continue
            if symbol.is_global():
                if symbol.is_referenced():
                    referenced_global_name_set.add(name)
                if symbol.is_assigned():
                    written_global_name_set.add(name)
        for child in table.get_children():
            visit(child)

    visit(module_table)
    free_name_set = referenced_global_name_set - module_defined_name_set
    return free_name_set, written_global_name_set


def _guard_call() -> ast.expr:
    return ast.Call(func=ast.Name(id=GUARD_NAME, ctx=ast.Load()), args=[], keywords=[])


def _guarded_expression(expression: ast.expr) -> ast.expr:
    """Evaluate the guard, then ``expression``: ``(__nighthawk_guard__(), expression)[1]``."""

    return ast.Subscript(
        value=ast.Tuple(elts=[_guard_call(), expression], ctx=ast.Load()),
        slice=ast.Constant(1),
        ctx=ast.Load(),
    )


def _forwarding_call(function_name: str, arguments: ast.arguments) -> ast.Call:
    """Call ``function_name`` forwarding every parameter of ``arguments`` unchanged."""

    positional: list[ast.expr] = [ast.Name(id=argument.arg, ctx=ast.Load()) for argument in (*arguments.posonlyargs, *arguments.args)]
    if arguments.vararg is not None:
        positional.append(ast.Starred(value=ast.Name(id=arguments.vararg.arg, ctx=ast.Load()), ctx=ast.Load()))
    keywords: list[ast.keyword] = [
        ast.keyword(arg=argument.arg, value=ast.Name(id=argument.arg, ctx=ast.Load())) for argument in arguments.kwonlyargs
    ]
    if arguments.kwarg is not None:
        keywords.append(ast.keyword(arg=None, value=ast.Name(id=arguments.kwarg.arg, ctx=ast.Load())))
    return ast.Call(func=ast.Name(id=function_name, ctx=ast.Load()), args=positional, keywords=keywords)


class _GuardInstrumenter(ast.NodeTransformer):
    """Insert the host execution guard wherever generated code can start or resume running.

    Function and method bodies, exception handlers, and ``finally`` blocks call
    the guard first; lambdas and generator expression elements and conditions
    evaluate it before their expression. A generator, async generator, or
    coroutine function is split into a hidden body function and a same-named
    function that returns the host proxy around the raw object, so that every
    resumption (``send``, ``throw``, ``close`` and their asynchronous
    counterparts, including the ones ``close()`` and garbage collection cause)
    is admitted by the guard before generated code continues.
    """

    def _wrap_suspendable(self, node: ast.FunctionDef | ast.AsyncFunctionDef, wrapper_name: str, *, asynchronous: bool) -> list[ast.stmt]:
        hidden_name = f"{_HIDDEN_BODY_NAME_PREFIX}{node.name}__"
        # The decorators (only the core Natural-function decorator is allowed)
        # stay with the body, which is where the Natural blocks are.
        hidden_type = ast.AsyncFunctionDef if isinstance(node, ast.AsyncFunctionDef) else ast.FunctionDef
        hidden = hidden_type(
            name=hidden_name,
            args=node.args,
            body=node.body,
            decorator_list=node.decorator_list,
            returns=node.returns,
            type_comment=None,
            type_params=[],
        )
        proxied = ast.Call(func=ast.Name(id=wrapper_name, ctx=ast.Load()), args=[_forwarding_call(hidden_name, node.args)], keywords=[])
        result: ast.expr = ast.Await(value=proxied) if asynchronous else proxied
        outer_body: list[ast.stmt] = [ast.Expr(value=_guard_call()), ast.Return(value=result)]
        outer_type = ast.AsyncFunctionDef if asynchronous else ast.FunctionDef
        outer = outer_type(
            name=node.name,
            args=node.args,
            body=outer_body,
            decorator_list=[],
            returns=node.returns,
            type_comment=None,
            type_params=[],
        )
        return [ast.copy_location(hidden, node), ast.copy_location(outer, node)]

    def _instrument_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.AST | list[ast.stmt]:
        self.generic_visit(node)
        node.body = [ast.Expr(value=_guard_call()), *node.body]
        is_generator = _contains_yield(node)
        if isinstance(node, ast.AsyncFunctionDef):
            return self._wrap_suspendable(node, WRAP_ASYNC_GENERATOR_NAME if is_generator else WRAP_COROUTINE_NAME, asynchronous=not is_generator)
        if is_generator:
            return self._wrap_suspendable(node, WRAP_GENERATOR_NAME, asynchronous=False)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST | list[ast.stmt]:
        return self._instrument_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST | list[ast.stmt]:
        return self._instrument_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> ast.AST:
        self.generic_visit(node)
        node.body = _guarded_expression(node.body)
        return node

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> ast.AST:
        self.generic_visit(node)
        node.elt = _guarded_expression(node.elt)
        for index, comprehension in enumerate(node.generators):
            if index > 0:
                comprehension.iter = _guarded_expression(comprehension.iter)
            comprehension.ifs = [_guarded_expression(condition) for condition in comprehension.ifs]
        return node

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> ast.AST:
        self.generic_visit(node)
        node.body = [ast.Expr(value=_guard_call()), *node.body]
        return node

    def _instrument_try(self, node: ast.Try | ast.TryStar) -> ast.AST:
        self.generic_visit(node)
        if node.finalbody:
            node.finalbody = [ast.Expr(value=_guard_call()), *node.finalbody]
        return node

    def visit_Try(self, node: ast.Try) -> ast.AST:
        return self._instrument_try(node)

    def visit_TryStar(self, node: ast.TryStar) -> ast.AST:
        return self._instrument_try(node)


def instrument_generated_source(module: ast.Module) -> str:
    """Return the source text of ``module`` with the host execution guard inserted.

    The text, not the tree, is what the host compiles and registers as the
    definition's source, so Natural functions the core decorator recompiles from
    source keep their guards.
    """

    instrumented = _GuardInstrumenter().visit(module)
    ast.fix_missing_locations(instrumented)
    return ast.unparse(instrumented) + "\n"


def analyze_generated_source(proposal: CodeProposal, *, filename: str) -> SourceAnalysis:
    """Validate generated source deterministically or raise :class:`SourceRejectedError`."""

    try:
        module = ast.parse(proposal.source, filename=filename)
    except SyntaxError as error:
        raise SourceRejectedError("source_syntax_error", str(error)) from error

    _validate_no_raw_dynamic_execution(module)
    defined_name_to_kind, imported_name_set = _collect_module_definitions(module)

    dependency_name_set = set(proposal.dependency_name_tuple)
    shadowed = sorted((set(defined_name_to_kind) | imported_name_set) & dependency_name_set)
    if shadowed:
        raise SourceRejectedError("dependency_shadowed", ", ".join(shadowed))

    export_name_set = set(proposal.export_name_tuple)
    for name in defined_name_to_kind:
        if name not in export_name_set and not name.startswith("_"):
            raise SourceRejectedError("undeclared_definition", name)

    export_name_to_kind: dict[str, DefinitionKind] = {}
    for export_name in proposal.export_name_tuple:
        kind = defined_name_to_kind.get(export_name)
        if kind is None:
            raise SourceRejectedError("export_undefined", export_name)
        if kind == "class":
            raise SourceRejectedError("export_kind_unsupported", f"{export_name} is a class")
        export_name_to_kind[export_name] = kind

    if proposal.execution_intent == "definition_and_invocation":
        assert proposal.entry_export_name is not None
        if export_name_to_kind[proposal.entry_export_name] != "function":
            raise SourceRejectedError("intent_mismatch", f"entry export {proposal.entry_export_name} is not a synchronous function")

    free_name_set, written_global_name_set = _collect_free_and_written_globals(proposal.source, filename)
    undeclared_dependency_set = free_name_set - dependency_name_set - _BUILTIN_NAME_SET - _MODULE_DUNDER_NAME_SET
    if undeclared_dependency_set:
        raise SourceRejectedError("undeclared_dependency", ", ".join(sorted(undeclared_dependency_set)))
    undeclared_write_set = written_global_name_set - proposal.effect_footprint.namespace_write_name_set
    if undeclared_write_set:
        raise SourceRejectedError("undeclared_namespace_write", ", ".join(sorted(undeclared_write_set)))

    export_name_to_docstring = {
        statement.name: ast.get_docstring(statement, clean=False)
        for statement in module.body
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef) and statement.name in export_name_set
    }
    return SourceAnalysis(
        export_name_to_kind=export_name_to_kind,
        free_name_tuple=tuple(sorted(free_name_set)),
        global_write_name_tuple=tuple(sorted(written_global_name_set)),
        instrumented_source=instrument_generated_source(module),
        export_name_to_docstring=export_name_to_docstring,
    )
