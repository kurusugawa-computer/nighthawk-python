import asyncio

import nighthawk as nh
from tests.support.executors import StubExecutor


def test_async_natural_function_allows_self_reference_freevar():
    nh.StepExecutorConfiguration()

    with nh.run(StubExecutor()):

        @nh.function
        async def f() -> int:
            """execute
            {"step_outcome": {"kind": "pass"}, "bindings": {}}
            """
            if False:
                return await f()
            return 17

        assert asyncio.run(f()) == 17


def test_natural_function_supports_instance_method():
    nh.StepExecutorConfiguration()

    with nh.run(StubExecutor()):

        class NaturalMethodCarrier:
            @nh.function
            def evaluate(self, value: int) -> int:
                """execute
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 31}}
                """
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert NaturalMethodCarrier().evaluate(10) == 31


def test_natural_function_supports_staticmethod_for_both_decorator_orders():
    nh.StepExecutorConfiguration()

    with nh.run(StubExecutor()):

        class NaturalStaticMethodCarrier:
            @staticmethod
            @nh.function
            def static_inner(value: int) -> int:
                """execute
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 41}}
                """
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            @nh.function
            @staticmethod
            def static_outer(value: int) -> int:
                """execute
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 42}}
                """
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert NaturalStaticMethodCarrier.static_inner(10) == 41
        assert NaturalStaticMethodCarrier.static_outer(10) == 42


def test_natural_function_supports_classmethod_for_both_decorator_orders():
    nh.StepExecutorConfiguration()

    with nh.run(StubExecutor()):

        class NaturalClassMethodCarrier:
            @classmethod
            @nh.function
            def class_inner(cls, value: int) -> int:
                """execute
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 51}}
                """
                _ = cls
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            @nh.function
            @classmethod
            def class_outer(cls, value: int) -> int:
                """execute
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 52}}
                """
                _ = cls
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert NaturalClassMethodCarrier.class_inner(10) == 51
        assert NaturalClassMethodCarrier.class_outer(10) == 52


def test_natural_function_supports_async_instance_method():
    nh.StepExecutorConfiguration()

    with nh.run(StubExecutor()):

        class NaturalAsyncMethodCarrier:
            @nh.function
            async def evaluate(self, value: int) -> int:
                """execute
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 61}}
                """
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert asyncio.run(NaturalAsyncMethodCarrier().evaluate(10)) == 61


def test_natural_function_supports_async_staticmethod_for_both_decorator_orders():
    nh.StepExecutorConfiguration()

    with nh.run(StubExecutor()):

        class NaturalAsyncStaticMethodCarrier:
            @staticmethod
            @nh.function
            async def static_inner(value: int) -> int:
                """execute
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 71}}
                """
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            @nh.function
            @staticmethod
            async def static_outer(value: int) -> int:
                """execute
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 72}}
                """
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert asyncio.run(NaturalAsyncStaticMethodCarrier.static_inner(10)) == 71
        assert asyncio.run(NaturalAsyncStaticMethodCarrier.static_outer(10)) == 72


def test_natural_function_supports_async_classmethod_for_both_decorator_orders():
    nh.StepExecutorConfiguration()

    with nh.run(StubExecutor()):

        class NaturalAsyncClassMethodCarrier:
            @classmethod
            @nh.function
            async def class_inner(cls, value: int) -> int:
                """execute
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 81}}
                """
                _ = cls
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

            @nh.function
            @classmethod
            async def class_outer(cls, value: int) -> int:
                """execute
                <value>
                <:result>
                {"step_outcome": {"kind": "pass"}, "bindings": {"result": 82}}
                """
                _ = cls
                _ = value
                return result  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        assert asyncio.run(NaturalAsyncClassMethodCarrier.class_inner(10)) == 81
        assert asyncio.run(NaturalAsyncClassMethodCarrier.class_outer(10)) == 82
