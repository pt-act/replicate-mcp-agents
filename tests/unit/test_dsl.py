"""Tests for replicate_mcp.dsl — safe expression DSL evaluator."""

from __future__ import annotations

import pytest

from replicate_mcp.dsl import (
    CompiledTransform,
    ExpressionSyntaxError,
    SafeEvaluator,
    UnsafeExpressionError,
    safe_eval,
)


class TestSafeEvaluatorBasicExpressions:
    """Test arithmetic, comparison, and string operations."""

    def test_arithmetic_add(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate("1 + 2") == 3

    def test_arithmetic_multiply(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate("3 * 4") == 12

    def test_arithmetic_floor_div(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate("10 // 3") == 3

    def test_comparison_less_than(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate("3 < 5") is True

    def test_comparison_with_context(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate("score > 0.7", {"score": 0.85}) is True

    def test_boolean_and(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate("True and False") is False

    def test_boolean_or(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate("True or False") is True

    def test_string_concat(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate('"hello " + "world"') == "hello world"

    def test_conditional_expression(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate("1 if True else 0") == 1

    def test_list_literal(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate("[1, 2, 3]") == [1, 2, 3]

    def test_dict_literal(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate('{"a": 1}') == {"a": 1}

    def test_subscript_dict(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate("data['key']", {"data": {"key": 42}}) == 42

    def test_subscript_list(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate("items[0]", {"items": [10, 20]}) == 10

    def test_len_builtin(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate("len(items)", {"items": [1, 2, 3]}) == 3

    def test_min_max_builtins(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate("min(a, b)", {"a": 3, "b": 7}) == 3

    def test_in_operator(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate("'x' in s", {"s": "xyz"}) is True

    def test_not_in_operator(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate("'q' not in s", {"s": "xyz"}) is True

    def test_list_comprehension(self) -> None:
        ev = SafeEvaluator()
        result = ev.evaluate("[x*2 for x in items]", {"items": [1, 2, 3]})
        assert result == [2, 4, 6]

    def test_method_call_str_upper(self) -> None:
        ev = SafeEvaluator()
        assert ev.evaluate("s.upper()", {"s": "hello"}) == "HELLO"


class TestSafeEvaluatorSecurity:
    """Ensure dangerous constructs are blocked."""

    def test_import_blocked(self) -> None:
        ev = SafeEvaluator()
        with pytest.raises(UnsafeExpressionError):
            ev.evaluate("__import__('os')")

    def test_exec_as_call_is_not_whitelisted(self) -> None:
        # exec is not in safe builtins — will raise NameError at eval time
        ev = SafeEvaluator()
        with pytest.raises(Exception):  # noqa: B017
            ev.evaluate("exec('x = 1')")

    def test_class_dunder_blocked(self) -> None:
        ev = SafeEvaluator()
        with pytest.raises(UnsafeExpressionError, match="dunder"):
            ev.evaluate("().__class__")

    def test_mro_dunder_blocked(self) -> None:
        ev = SafeEvaluator()
        with pytest.raises(UnsafeExpressionError, match="dunder"):
            ev.evaluate("str.__mro__")

    def test_dunder_name_blocked(self) -> None:
        ev = SafeEvaluator()
        with pytest.raises(UnsafeExpressionError, match="dunder"):
            ev.evaluate("__builtins__")

    def test_lambda_blocked(self) -> None:
        ev = SafeEvaluator()
        with pytest.raises(UnsafeExpressionError):
            ev.evaluate("lambda x: x")

    def test_fstring_blocked_by_default(self) -> None:
        ev = SafeEvaluator()
        with pytest.raises(UnsafeExpressionError, match="f-string"):
            ev.evaluate('f"hello {name}"', {"name": "world"})

    def test_fstring_allowed_when_enabled(self) -> None:
        ev = SafeEvaluator(allow_fstrings=True)
        result = ev.evaluate('f"hello {name}"', {"name": "world"})
        assert result == "hello world"


class TestSafeEvaluatorErrors:
    """Test error types for bad syntax and invalid constructs."""

    def test_syntax_error_raises_expression_syntax_error(self) -> None:
        ev = SafeEvaluator()
        with pytest.raises(ExpressionSyntaxError):
            ev.evaluate("if True:")

    def test_validate_raises_but_does_not_execute(self) -> None:
        ev = SafeEvaluator()
        with pytest.raises(UnsafeExpressionError):
            ev.validate("__import__('os')")

    def test_name_error_propagated(self) -> None:
        ev = SafeEvaluator()
        with pytest.raises(NameError):
            ev.evaluate("undefined_variable")


class TestCompiledTransform:
    """Test the pre-compiled transform facility."""

    def test_compile_and_call(self) -> None:
        ev = SafeEvaluator()
        t = ev.compile_transform("x + y")
        assert t({"x": 3, "y": 4}) == 7

    def test_compiled_transform_reusable(self) -> None:
        ev = SafeEvaluator()
        t = ev.compile_transform("x * 2")
        assert t({"x": 5}) == 10
        assert t({"x": 0}) == 0

    def test_compiled_transform_repr(self) -> None:
        ev = SafeEvaluator()
        t = ev.compile_transform("x + 1")
        assert "CompiledTransform" in repr(t)
        assert "x + 1" in repr(t)

    def test_compiled_transform_no_context(self) -> None:
        ev = SafeEvaluator()
        t = ev.compile_transform("1 + 1")
        assert t() == 2


class TestSafeEvalShorthand:
    """Test the module-level safe_eval convenience function."""

    def test_basic_eval(self) -> None:
        assert safe_eval("2 ** 10") == 1024

    def test_with_context(self) -> None:
        assert safe_eval("a + b", {"a": 10, "b": 5}) == 15

    def test_blocks_dangerous(self) -> None:
        with pytest.raises(UnsafeExpressionError):
            safe_eval("__import__('os')")