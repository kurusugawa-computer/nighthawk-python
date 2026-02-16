import ast
import inspect
import logging
import textwrap

import nighthawk as nh


class _FakeRunResult:
    def __init__(self, output):
        self.output = output


class _FakeAgent:
    def __init__(self):
        self.seen_prompts: list[str] = []

    def run_sync(self, user_prompt, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
        from nighthawk.execution.contracts import PassOutcome

        self.seen_prompts.append(user_prompt)
        assert deps is not None
        _ = kwargs
        return _FakeRunResult(PassOutcome(kind="pass"))


def test_natural_traceback_includes_docstring_sentinel_line(tmp_path):
    agent = _FakeAgent()
    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=nh.ExecutionConfiguration(),
            execution_executor=nh.AgentExecutor(agent=agent),
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> None:
            """natural
            <missing_name>
            """

        try:
            f()
            assert False, "Expected NameError"
        except NameError as e:
            error = e

    frames = inspect.getinnerframes(error.__traceback__)  # type: ignore

    wrapped = getattr(f, "__wrapped__", None)
    assert callable(wrapped)

    wrapped_lines, wrapped_start_lineno = inspect.getsourcelines(wrapped)
    expected_natural_lineno = next(lineno for lineno, line_text in enumerate(wrapped_lines, start=wrapped_start_lineno) if '"""natural' in line_text)

    natural_frame = next(
        (frame for frame in frames if frame.filename == __file__ and frame.lineno == expected_natural_lineno),
        None,
    )
    if natural_frame is None:
        logging.info(
            "Traceback frames: %s",
            [(frame.filename, frame.lineno, frame.function) for frame in frames],
        )

    assert natural_frame is not None


def test_natural_traceback_includes_inline_block_line(tmp_path):
    agent = _FakeAgent()
    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=nh.ExecutionConfiguration(),
            execution_executor=nh.AgentExecutor(agent=agent),
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> None:
            x = 10
            """natural
            Say hi.
            """

            """natural
            <missing_name>
            """

            _ = x

        try:
            f()
            assert False, "Expected NameError"
        except NameError as e:
            error = e

    frames = inspect.getinnerframes(error.__traceback__)  # type: ignore

    wrapped = getattr(f, "__wrapped__", None)
    assert callable(wrapped)

    wrapped_lines, wrapped_start_lineno = inspect.getsourcelines(wrapped)

    module_source = textwrap.dedent("".join(wrapped_lines))
    module = ast.parse(module_source)

    function_def = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "f")

    inline_natural_line_numbers = [statement.lineno for statement in function_def.body if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str) and statement.value.value.startswith("natural\n")]
    assert len(inline_natural_line_numbers) >= 2

    # The second Natural block contains the missing binding reference.
    expected_natural_lineno = wrapped_start_lineno - 1 + inline_natural_line_numbers[1]

    natural_frame = next(
        (frame for frame in frames if frame.filename == __file__ and frame.lineno == expected_natural_lineno),
        None,
    )
    if natural_frame is None:
        logging.info(
            "Traceback frames: %s",
            [(frame.filename, frame.lineno, frame.function) for frame in frames],
        )

    assert natural_frame is not None


def test_natural_traceback_includes_location_on_executor_exception(tmp_path):
    class _FailingExecutor:
        def run_natural_block(
            self,
            *,
            processed_natural_program: str,
            execution_context: object,
            binding_names: list[str],
            allowed_outcome_kinds: tuple[str, ...],
        ):
            _ = (
                processed_natural_program,
                execution_context,
                binding_names,
                allowed_outcome_kinds,
            )
            raise RuntimeError("Executor failed")

    with nh.environment(
        nh.ExecutionEnvironment(
            execution_configuration=nh.ExecutionConfiguration(),
            execution_executor=_FailingExecutor(),
            workspace_root=tmp_path,
        )
    ):

        @nh.fn
        def f() -> None:
            """natural
            Say hi.
            """

        try:
            f()
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            error = e

    frames = inspect.getinnerframes(error.__traceback__)  # type: ignore[arg-type]

    wrapped = getattr(f, "__wrapped__", None)
    assert callable(wrapped)

    wrapped_lines, wrapped_start_lineno = inspect.getsourcelines(wrapped)
    expected_natural_lineno = next(lineno for lineno, line_text in enumerate(wrapped_lines, start=wrapped_start_lineno) if '"""natural' in line_text)

    natural_frame = next(
        (frame for frame in frames if frame.filename == __file__ and frame.lineno == expected_natural_lineno),
        None,
    )

    if natural_frame is None:
        logging.info(
            "Traceback frames: %s",
            [(frame.filename, frame.lineno, frame.function) for frame in frames],
        )

    assert natural_frame is not None
