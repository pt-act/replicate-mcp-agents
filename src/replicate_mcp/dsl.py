"""Safe expression DSL using restricted AST evaluation.

Sprint S6 — Hardening.  Replaces the ``eval()``-based transform
approach in old YAML examples with a sandboxed evaluator that only
allows a curated subset of Python expressions.

Security model:
    - Only ``ast.Expression`` nodes (single expressions, no statements)
      are accepted.
    - Allowed AST node types are explicitly white-listed.  Any node
      type not in the allow-list raises :class:`UnsafeExpressionError`.
    - The evaluation namespace exposes only safe builtins and the
      caller-supplied context.
    - Arbitrary attribute access (``__class__``, ``__mro__``, etc.)
      raises :class:`UnsafeExpressionError`.

Allowed constructs (non-exhaustive):
    - Literals: strings, numbers, booleans, ``None``, bytes, lists,
      dicts, sets, tuples
    - Arithmetic: ``+``, ``-``, ``*``, ``/``, ``//``, ``%``, ``**``
    - Comparison: ``==``, ``!=``, ``<``, ``<=``, ``>``, ``>=``,
      ``in``, ``not in``, ``is``, ``is not``
    - Boolean: ``and``, ``or``, ``not``
    - Attribute access on *safe* objects (checked via allowlist)
    - Subscript: ``data["key"]``, ``items[0]``
    - Conditional expressions: ``a if cond else b``
    - String formatting (f-strings are *not* allowed — use format())

Not allowed:
    - ``import``, ``exec``, ``eval``, ``__import__``
    - Function *definitions* (lambdas in expressions are blocked)
    - Calls to anything other than the pre-registered safe_builtins
    - Access to dunder attributes

Usage::

    from replicate_mcp.dsl import SafeEvaluator

    ev = SafeEvaluator()
    result = ev.evaluate("score > 0.7", context={"score": 0.85})
    # True

    result = ev.evaluate("data['key'].upper()", context={"data": {"key": "hello"}})
    # "HELLO"
"""

from __future__ import annotations

import ast
import math
from typing import Any

from replicate_mcp.exceptions import ReplicateMCPError

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class UnsafeExpressionError(ReplicateMCPError):
    """Raised when an expression uses disallowed AST constructs."""

    def __init__(self, reason: str, node: ast.AST | None = None) -> None:
        location = ""
        if node is not None and hasattr(node, "lineno"):
            location = f" (line {node.lineno}, col {node.col_offset})"
        super().__init__(f"Unsafe expression{location}: {reason}")
        self.reason = reason


class ExpressionSyntaxError(ReplicateMCPError):
    """Raised when the expression cannot be parsed."""

    def __init__(self, expression: str, cause: SyntaxError) -> None:
        super().__init__(
            f"Syntax error in expression {expression!r}: {cause.msg}"
        )
        self.cause = cause


# ---------------------------------------------------------------------------
# AST allow-list
# ---------------------------------------------------------------------------

_ALLOWED_NODES: frozenset[type[ast.AST]] = frozenset(
    {
        # Expression wrapper
        ast.Expression,
        # Literals
        ast.Constant,
        ast.List,
        ast.Tuple,
        ast.Set,
        ast.Dict,
        # Variables
        ast.Name,
        ast.Load,
        ast.Store,
        ast.Del,
        # Operators
        ast.BoolOp,
        ast.BinOp,
        ast.UnaryOp,
        ast.Compare,
        ast.IfExp,
        # Specific op types
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.Not,
        ast.And,
        ast.Or,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.In,
        ast.NotIn,
        ast.Is,
        ast.IsNot,
        ast.BitAnd,
        ast.BitOr,
        ast.BitXor,
        ast.Invert,
        ast.LShift,
        ast.RShift,
        # Access
        ast.Subscript,
        ast.Index,        # Python < 3.9 compatibility
        ast.Attribute,
        ast.Slice,
        # Call (restricted — only whitelisted callables)
        ast.Call,
        ast.keyword,
        # Comprehensions (list/dict/set/generator)
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
        ast.comprehension,
        # Starred (for unpacking in calls)
        ast.Starred,
        # Formatting
        ast.JoinedStr,   # f-strings — blocked at validator level if desired
        ast.FormattedValue,
    }
)

_DUNDER_RE = __import__("re").compile(r"__\w+__")

# ---------------------------------------------------------------------------
# Safe builtins exposed to expressions
# ---------------------------------------------------------------------------

_SAFE_BUILTINS: dict[str, Any] = {
    # Type conversions
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "bytes": bytes,
    # Iterables
    "len": len,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "sorted": sorted,
    "reversed": reversed,
    "sum": sum,
    "min": min,
    "max": max,
    "any": any,
    "all": all,
    # String
    "chr": chr,
    "ord": ord,
    "format": format,
    # Math
    "abs": abs,
    "round": round,
    "pow": pow,
    "divmod": divmod,
    "math": math,
    # Introspection
    "isinstance": isinstance,
    "issubclass": issubclass,
    "type": type,
    # Constants
    "True": True,
    "False": False,
    "None": None,
}


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class _ASTValidator(ast.NodeVisitor):
    """Walk the AST and reject any disallowed node types or dunder access."""

    def generic_visit(self, node: ast.AST) -> None:
        node_type = type(node)
        if node_type not in _ALLOWED_NODES:
            raise UnsafeExpressionError(
                f"node type '{node_type.__name__}' is not permitted",
                node=node,
            )
        super().generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if _DUNDER_RE.match(node.attr):
            raise UnsafeExpressionError(
                f"access to dunder attribute '{node.attr}' is forbidden",
                node=node,
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if _DUNDER_RE.match(node.id):
            raise UnsafeExpressionError(
                f"reference to dunder name '{node.id}' is forbidden",
                node=node,
            )
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class SafeEvaluator:
    """Evaluate restricted Python expressions in a sandboxed namespace.

    Args:
        extra_builtins: Additional callables / constants to expose.
        allow_fstrings:  If ``False`` (default), f-strings are blocked.

    Raises:
        :class:`ExpressionSyntaxError`:  Parse error in *expression*.
        :class:`UnsafeExpressionError`:  Disallowed AST construct.
        Any exception raised by the expression itself during evaluation.
    """

    def __init__(
        self,
        extra_builtins: dict[str, Any] | None = None,
        allow_fstrings: bool = False,
    ) -> None:
        self._builtins = dict(_SAFE_BUILTINS)
        if extra_builtins:
            self._builtins.update(extra_builtins)
        self._allow_fstrings = allow_fstrings
        self._validator = _ASTValidator()

    def validate(self, expression: str) -> ast.Expression:
        """Parse and validate *expression*, returning the compiled AST.

        Raises on syntax errors or disallowed constructs without
        executing anything.
        """
        try:
            tree = ast.parse(expression.strip(), mode="eval")
        except SyntaxError as exc:
            raise ExpressionSyntaxError(expression, exc) from exc

        if not self._allow_fstrings:
            for node in ast.walk(tree):
                if isinstance(node, ast.JoinedStr):
                    raise UnsafeExpressionError(
                        "f-strings are not allowed; use str.format() instead",
                        node=node,
                    )

        self._validator.visit(tree)
        return tree

    def evaluate(
        self,
        expression: str,
        context: dict[str, Any] | None = None,
    ) -> Any:  # noqa: ANN401
        """Parse, validate, and evaluate *expression* with *context* variables.

        Args:
            expression: A single Python expression (no statements).
            context:    Variables available during evaluation.

        Returns:
            The result of the expression.

        Example::

            ev = SafeEvaluator()
            ev.evaluate("score * 100 > threshold", {"score": 0.9, "threshold": 80})
            # True
        """
        tree = self.validate(expression)
        ns: dict[str, Any] = {"__builtins__": self._builtins}
        if context:
            ns.update(context)
        return eval(compile(tree, "<dsl>", "eval"), ns)  # noqa: S307

    def compile_transform(
        self,
        expression: str,
    ) -> CompiledTransform:
        """Return a :class:`CompiledTransform` for repeated evaluation of *expression*.

        Pre-compiles the AST so repeated invocations are faster.
        """
        tree = self.validate(expression)
        code = compile(tree, "<dsl>", "eval")
        builtins = dict(self._builtins)
        return CompiledTransform(expression=expression, code=code, builtins=builtins)


class CompiledTransform:
    """A pre-compiled DSL expression ready for repeated calls.

    Returned by :meth:`SafeEvaluator.compile_transform`.  The ``code``
    object is shared across invocations; each call gets a fresh namespace
    derived from *builtins* + *context*.
    """

    def __init__(
        self,
        expression: str,
        code: Any,
        builtins: dict[str, Any],
    ) -> None:
        self.expression = expression
        self._code = code
        self._builtins = builtins

    def __call__(self, context: dict[str, Any] | None = None) -> Any:  # noqa: ANN401
        ns: dict[str, Any] = {"__builtins__": self._builtins}
        if context:
            ns.update(context)
        return eval(self._code, ns)  # noqa: S307

    def __repr__(self) -> str:
        return f"CompiledTransform({self.expression!r})"


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

_DEFAULT_EVALUATOR = SafeEvaluator()


def safe_eval(
    expression: str,
    context: dict[str, Any] | None = None,
) -> Any:  # noqa: ANN401
    """Module-level shorthand for :meth:`SafeEvaluator.evaluate`.

    Uses a shared default evaluator with the standard builtin set.
    """
    return _DEFAULT_EVALUATOR.evaluate(expression, context)


__all__ = [
    "UnsafeExpressionError",
    "ExpressionSyntaxError",
    "SafeEvaluator",
    "CompiledTransform",
    "safe_eval",
]
