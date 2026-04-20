"""Unit tests for the safe transform registry."""

from __future__ import annotations

import pytest

from replicate_mcp.agents.transforms import TransformRegistry, default_registry


class TestTransformRegistry:
    """Tests for TransformRegistry core functionality."""

    def test_register_and_get_transform(self) -> None:
        reg = TransformRegistry()

        @reg.transform("double")
        def _double(data: dict) -> dict:
            return {k: v * 2 for k, v in data.items()}

        fn = reg.get_transform("double")
        assert fn({"x": 3}) == {"x": 6}

    def test_register_and_get_condition(self) -> None:
        reg = TransformRegistry()

        @reg.condition("is_positive")
        def _is_pos(data: dict) -> bool:
            return data.get("value", 0) > 0

        fn = reg.get_condition("is_positive")
        assert fn({"value": 5}) is True
        assert fn({"value": -1}) is False

    def test_get_missing_transform_raises(self) -> None:
        reg = TransformRegistry()
        with pytest.raises(KeyError, match="not found"):
            reg.get_transform("nope")

    def test_get_missing_condition_raises(self) -> None:
        reg = TransformRegistry()
        with pytest.raises(KeyError, match="not found"):
            reg.get_condition("nope")

    def test_duplicate_transform_raises(self) -> None:
        reg = TransformRegistry()

        @reg.transform("dup")
        def _first(data: dict) -> dict:
            return data

        with pytest.raises(ValueError, match="already registered"):

            @reg.transform("dup")
            def _second(data: dict) -> dict:
                return data

    def test_duplicate_condition_raises(self) -> None:
        reg = TransformRegistry()

        @reg.condition("dup")
        def _first(data: dict) -> bool:
            return True

        with pytest.raises(ValueError, match="already registered"):

            @reg.condition("dup")
            def _second(data: dict) -> bool:
                return False

    def test_transform_names(self) -> None:
        reg = TransformRegistry()

        @reg.transform("beta")
        def _b(d: dict) -> dict:
            return d

        @reg.transform("alpha")
        def _a(d: dict) -> dict:
            return d

        assert reg.transform_names == ["alpha", "beta"]

    def test_condition_names(self) -> None:
        reg = TransformRegistry()

        @reg.condition("z_cond")
        def _z(d: dict) -> bool:
            return True

        @reg.condition("a_cond")
        def _a(d: dict) -> bool:
            return True

        assert reg.condition_names == ["a_cond", "z_cond"]


class TestDefaultRegistry:
    """Tests for the built-in default_registry and its transforms."""

    def test_extract_prompt(self) -> None:
        fn = default_registry.get_transform("extract_prompt")
        result = fn({"enhanced_prompt": "a cat"})
        assert result == {"prompt": "a cat"}

    def test_extract_query(self) -> None:
        fn = default_registry.get_transform("extract_query")
        assert fn({"topic": "AI safety"}) == {"query": "AI safety"}

    def test_extract_sources(self) -> None:
        fn = default_registry.get_transform("extract_sources")
        assert fn({"results": [1, 2]}) == {"sources": [1, 2]}

    def test_extract_analysis(self) -> None:
        fn = default_registry.get_transform("extract_analysis")
        assert fn({"report": "R"}) == {"analysis": "R"}

    def test_passthrough(self) -> None:
        fn = default_registry.get_transform("passthrough")
        data = {"any": "thing"}
        assert fn(data) == data

    def test_always_condition(self) -> None:
        fn = default_registry.get_condition("always")
        assert fn({}) is True
        assert fn({"anything": "here"}) is True

    def test_quality_above_07(self) -> None:
        fn = default_registry.get_condition("quality_above_0_7")
        assert fn({"quality_threshold": 0.8}) is True
        assert fn({"quality_threshold": 0.5}) is False
        assert fn({}) is False  # missing key → 0 → False

    def test_no_eval_in_codebase(self) -> None:
        """Verify that no eval() or exec() calls exist in executable code.

        Uses the ``ast`` module to inspect function bodies, which
        naturally ignores docstrings and comments.
        """
        import ast

        import replicate_mcp.agents.transforms as mod

        path = mod.__file__
        assert path is not None
        tree = ast.parse(open(path).read())  # noqa: SIM115

        dangerous_names = {"eval", "exec"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name in dangerous_names:
                    raise AssertionError(
                        f"Dangerous call '{name}()' found at line {node.lineno}"
                    )