"""Restricted parsing of SymPy expressions written by the host AI.

Defence in depth, layer 1 (layer 2 is the isolated worker process with a timeout):

* tokenisation with the Python tokenizer; only NAME, NUMBER and a fixed set of
  operators are accepted — no strings, no attribute access (``.``), no lambdas,
  comprehensions or keywords;
* names starting with ``_`` and a deny-list of dangerous builtins are rejected;
* evaluation uses an explicit global namespace with ``__builtins__`` emptied;
* size limits on the expression and its integer literals.
"""

from __future__ import annotations

import io
import keyword
import token
import tokenize
from typing import Any

import sympy as sp
from sympy.parsing.sympy_parser import (
    auto_number,
    auto_symbol,
    convert_xor,
    factorial_notation,
    parse_expr,
    repeated_decimals,
)

MAX_LEN = 4000
MAX_INT_DIGITS = 12

_ALLOWED_OPS = {
    "+", "-", "*", "/", "**", "(", ")", ",", "[", "]", "<", ">", "<=", ">=", "==", "!=", "^", "@", "!", "=",
}
_DENY = {
    "eval", "exec", "open", "compile", "globals", "locals", "vars", "getattr", "setattr", "delattr", "input",
    "breakpoint", "help", "type", "object", "super", "classmethod", "staticmethod", "property", "memoryview",
    "bytearray", "bytes", "dir", "id", "hash", "iter", "next", "os", "sys", "subprocess", "socket", "builtins",
    "importlib", "pickle", "marshal", "ctypes", "file", "exit", "quit", "print", "format", "chr", "ord",
    "Symbol", "Function", "sympify", "S", "lambdify", "evaluate", "init_printing", "var", "symbols",
}


def _matrix_inverse(m):
    return m.inv()


def _transpose(m):
    return m.T


def _dot(a, b):
    if isinstance(a, sp.MatrixBase) and isinstance(b, sp.MatrixBase):
        return (a.T * b)[0, 0] if a.shape[1] == 1 and b.shape[1] == 1 else a.dot(b)
    return a * b


def _norm(v):
    return v.norm() if isinstance(v, sp.MatrixBase) else sp.Abs(v)


def _outer(a, b):
    return a * b.T


def _hessian(expr, variables):
    return sp.hessian(expr, list(variables))


def _jacobian(exprs, variables):
    return sp.Matrix(list(exprs)).jacobian(list(variables))


def _grad(expr, variables):
    return sp.Matrix([sp.diff(expr, v) for v in variables])


def _sigmoid(x):
    return 1 / (1 + sp.exp(-x))


def _softplus(x):
    return sp.log(1 + sp.exp(x))


def _relu(x):
    return sp.Max(0, x)


SAFE_NAMESPACE: dict[str, Any] = {
    # elementary functions
    **{name: getattr(sp, name) for name in (
        "sin", "cos", "tan", "cot", "sec", "csc", "asin", "acos", "atan", "atan2", "sinh", "cosh", "tanh",
        "asinh", "acosh", "atanh", "exp", "log", "sqrt", "cbrt", "root", "Abs", "sign", "floor", "ceiling",
        "factorial", "binomial", "gamma", "beta", "erf", "erfc", "Max", "Min", "Heaviside", "DiracDelta",
        "re", "im", "conjugate", "arg", "Piecewise", "KroneckerDelta", "zeta", "polygamma", "loggamma",
        "diff", "integrate", "limit", "Sum", "Product", "Derivative", "Integral", "Rational", "Matrix", "eye",
        "zeros", "ones", "det", "trace", "Eq", "Ne", "Lt", "Le", "Gt", "Ge", "And", "Or", "Not", "oo", "pi",
        "E", "I", "nan", "zoo", "simplify", "expand", "factor", "series", "summation", "hessian", "Interval",
        "Union", "FiniteSet", "EmptySet", "Reals", "Integers", "Naturals", "Complexes", "Mod", "gcd", "lcm",
        "Float", "Integer",
    )},
    "ln": sp.log,
    "abs": sp.Abs,
    "transpose": _transpose,
    "inverse": _matrix_inverse,
    "inv": _matrix_inverse,
    "dot": _dot,
    "norm": _norm,
    "outer": _outer,
    "jacobian": _jacobian,
    "gradient": _grad,
    "sigmoid": _sigmoid,
    "softplus": _softplus,
    "relu": _relu,
    "True": sp.true,
    "False": sp.false,
}

TRANSFORMS = (auto_symbol, repeated_decimals, auto_number, factorial_notation, convert_xor)


class UnsafeExpression(ValueError):
    pass


def validate_tokens(source: str, extra_names: set[str] | None = None) -> None:
    if len(source) > MAX_LEN:
        raise UnsafeExpression(f"expression longer than {MAX_LEN} characters")
    if "\n" in source.strip() or ";" in source:
        raise UnsafeExpression("only single expressions are allowed")
    depth = 0
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        raise UnsafeExpression(f"cannot tokenize expression: {exc}") from exc
    for tok in tokens:
        if tok.type in (token.NEWLINE, token.NL, token.ENDMARKER, token.INDENT, token.DEDENT):
            continue
        if tok.type == token.NUMBER:
            digits = tok.string.replace("_", "")
            if digits.isdigit() and len(digits) > MAX_INT_DIGITS:
                raise UnsafeExpression(f"integer literal too large: {tok.string[:20]}…")
            if digits.lower().startswith(("0x", "0o", "0b")) or "j" in digits.lower():
                raise UnsafeExpression(f"unsupported numeric literal {tok.string}")
            continue
        if tok.type == token.NAME:
            name = tok.string
            if name.startswith("_") or "__" in name:
                raise UnsafeExpression(f"name {name!r} is not allowed")
            if keyword.iskeyword(name) and name not in ("True", "False"):
                raise UnsafeExpression(f"keyword {name!r} is not allowed")
            if name in _DENY and not (extra_names and name in extra_names):
                raise UnsafeExpression(f"name {name!r} is not allowed")
            continue
        if tok.type == token.OP:
            if tok.string not in _ALLOWED_OPS:
                raise UnsafeExpression(f"operator {tok.string!r} is not allowed")
            if tok.string in ("(", "["):
                depth += 1
                if depth > 60:
                    raise UnsafeExpression("expression nested too deeply")
            elif tok.string in (")", "]"):
                depth -= 1
            continue
        if tok.type == token.ERRORTOKEN and tok.string.strip() in ("", "!"):
            continue
        raise UnsafeExpression(f"token {tok.string!r} is not allowed")


def make_symbols(assumptions: dict[str, dict[str, bool]]) -> dict[str, sp.Symbol]:
    out = {}
    for name, flags in assumptions.items():
        validate_tokens(name)
        if not name.isidentifier():
            raise UnsafeExpression(f"invalid symbol name {name!r}")
        clean = {k: bool(v) for k, v in (flags or {}).items() if k in {
            "real", "positive", "negative", "nonnegative", "nonpositive", "integer", "nonzero", "finite",
            "complex", "rational", "even", "odd", "prime",
        }}
        out[name] = sp.Symbol(name, **clean)
    return out


def safe_parse(source: str, local: dict[str, Any] | None = None) -> Any:
    """Parse ``source`` into a SymPy object using only whitelisted names."""
    if not isinstance(source, str):
        source = str(source)
    local = dict(local or {})
    validate_tokens(source, extra_names=set(local))
    # Python's '=' would be an assignment: accept 'a = b' as an equation only via Eq(); reject bare '='.
    if "=" in source.replace("==", "").replace("<=", "").replace(">=", "").replace("!=", ""):
        raise UnsafeExpression("use Eq(a, b) or '==' instead of '='")
    global_ns: dict[str, Any] = {"__builtins__": {}}
    global_ns.update(SAFE_NAMESPACE)
    global_ns.update({"Integer": sp.Integer, "Float": sp.Float, "Rational": sp.Rational, "Symbol": sp.Symbol})
    try:
        return parse_expr(source, local_dict=local, global_dict=global_ns, transformations=TRANSFORMS, evaluate=True)
    except UnsafeExpression:
        raise
    except Exception as exc:
        raise ValueError(f"cannot parse {source!r}: {type(exc).__name__}: {exc}") from exc
