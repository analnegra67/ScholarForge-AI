"""Check implementations. Runs inside the isolated worker process only.

Outcomes:
* ``pass`` + method ``symbolic``  – SymPy proved the statement (exact simplification);
* ``pass`` + method ``numeric``   – no symbolic proof, but agreement at many random points
  (evidence, not proof);
* ``pass`` + method ``dimensional`` – dimensional homogeneity holds;
* ``fail`` – a counterexample or a mismatch was found;
* ``inconclusive`` – neither proof nor counterexample (e.g. evaluation failed);
* ``error`` – the specification itself is invalid.
"""

from __future__ import annotations

import math
import random
from typing import Any

import mpmath
import sympy as sp

from scholarforge.science.mathcheck import dimensions as dimmod
from scholarforge.science.mathcheck.safe_parse import UnsafeExpression, make_symbols, safe_parse

DEFAULT_SAMPLES = 24
DEFAULT_TOL = 1e-8


class SpecError(ValueError):
    pass


def _result(outcome: str, method: str, message: str, **details: Any) -> dict[str, Any]:
    return {"outcome": outcome, "method": method, "message": message, "details": _jsonable(details)}


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


class Context:
    def __init__(self, spec: dict[str, Any], samples: int, tol: float):
        self.spec = spec
        self.samples = samples
        self.tol = spec.get("tolerance") or tol
        self.local: dict[str, Any] = {}
        # Symbols named in 'dimensions' are physical quantities: they shadow constants such as E (energy, not e).
        for name in (spec.get("dimensions") or {}):
            if not str(name).isidentifier() or str(name).startswith("_"):
                raise SpecError(f"invalid symbol name {name!r}")
            self.local[name] = sp.Symbol(name, positive=True)
        self.local.update(make_symbols(spec.get("symbols") or {}))
        for fname in spec.get("functions") or []:
            if not str(fname).isidentifier() or str(fname).startswith("_"):
                raise SpecError(f"invalid function name {fname!r}")
            self.local[fname] = sp.Function(fname)
        for mname, shape in (spec.get("matrices") or {}).items():
            if not str(mname).isidentifier() or str(mname).startswith("_"):
                raise SpecError(f"invalid matrix name {mname!r}")
            if not (isinstance(shape, list) and len(shape) == 2 and all(isinstance(n, int) and 1 <= n <= 8 for n in shape)):
                raise SpecError(f"matrix {mname} needs shape [rows, cols] with 1..8 entries each")
            self.local[mname] = sp.Matrix(shape[0], shape[1],
                                          lambda i, j, m=mname: sp.Symbol(f"{m}_{i + 1}{j + 1}", real=True))

    def parse(self, source: Any) -> Any:
        if source is None:
            raise SpecError("missing expression")
        return safe_parse(str(source), self.local)

    def var(self, name: Any) -> sp.Symbol:
        if isinstance(name, list):
            raise SpecError("expected a single variable")
        sym = self.parse(name)
        if not isinstance(sym, sp.Symbol):
            raise SpecError(f"variable {name!r} is not a symbol")
        return sym

    def vars(self, names: Any) -> list[sp.Symbol]:
        if isinstance(names, str):
            names = [n.strip() for n in names.split(",") if n.strip()]
        return [self.var(n) for n in names]


# --------------------------------------------------------------------------------- numeric helpers
def _sample_value(sym: sp.Symbol, domain: dict[str, list[float]], rng: random.Random) -> Any:
    name = str(sym)
    if name in domain:
        lo, hi = domain[name]
        if sym.is_integer:
            return sp.Integer(rng.randint(int(math.ceil(lo)), int(math.floor(hi))))
        return sp.Float(rng.uniform(lo, hi), 30)
    if sym.is_integer:
        lo = 1 if (sym.is_positive or sym.is_nonnegative) else -6
        return sp.Integer(rng.randint(lo, 7))
    if sym.is_positive:
        return sp.Float(rng.uniform(0.05, 3.0), 30)
    if sym.is_negative:
        return sp.Float(-rng.uniform(0.05, 3.0), 30)
    if sym.is_nonnegative:
        return sp.Float(rng.uniform(0.0, 3.0), 30)
    return sp.Float(rng.uniform(-2.5, 2.5), 30)


def _to_number(value: Any) -> complex | None:
    try:
        v = complex(sp.N(value, 30))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    if any(math.isnan(x) or math.isinf(x) for x in (v.real, v.imag)):
        return None
    return v


def _close(a: complex, b: complex, tol: float) -> bool:
    """Relative comparison; values are evaluated with 30 significant digits, so tol=1e-8 is generous."""
    scale = max(1.0, abs(a), abs(b))
    return abs(a - b) <= tol * scale


def numeric_compare(lhs: Any, rhs: Any, ctx: Context, *, seed: int = 1234) -> tuple[str, dict]:
    """Compare two scalar (or matrix) expressions at random points."""
    rng = random.Random(seed)
    symbols = sorted((lhs - rhs).free_symbols if not isinstance(lhs, sp.MatrixBase) else
                     set().union(*[e.free_symbols for e in (lhs - rhs)]), key=str)
    domain = ctx.spec.get("domain") or {}
    valid = 0
    tried = 0
    worst = 0.0
    while valid < ctx.samples and tried < ctx.samples * 6:
        tried += 1
        point = {s: _sample_value(s, domain, rng) for s in symbols}
        try:
            if isinstance(lhs, sp.MatrixBase):
                lv = [_to_number(e) for e in lhs.subs(point)]
                rv = [_to_number(e) for e in rhs.subs(point)]
                if any(x is None for x in lv + rv):
                    continue
                pairs = list(zip(lv, rv))
            else:
                a, b = _to_number(lhs.subs(point)), _to_number(rhs.subs(point))
                if a is None or b is None:
                    continue
                pairs = [(a, b)]
        except Exception:
            continue
        valid += 1
        for a, b in pairs:
            diff = abs(a - b) / max(1.0, abs(a), abs(b))
            worst = max(worst, diff)
            if not _close(a, b, ctx.tol):
                return "fail", {"counterexample": {str(k): str(v) for k, v in point.items()},
                                "lhs_value": str(a), "rhs_value": str(b)}
    if valid < max(3, ctx.samples // 3):
        return "inconclusive", {"valid_points": valid, "attempted": tried}
    return "pass", {"valid_points": valid, "max_relative_difference": worst}


def _is_zero_symbolically(expr: Any) -> bool | None:
    try:
        if isinstance(expr, sp.MatrixBase):
            return all(_is_zero_symbolically(e) is True for e in expr) or None
        for transform in (lambda e: e, sp.expand, sp.simplify, sp.trigsimp, sp.powsimp, lambda e: sp.expand_log(e, force=True),
                          sp.radsimp, sp.cancel, sp.together):
            try:
                if sp.simplify(transform(expr)) == 0:
                    return True
            except Exception:
                continue
        return None
    except Exception:
        return None


def compare(lhs: Any, rhs: Any, ctx: Context, what: str) -> dict[str, Any]:
    if isinstance(lhs, sp.MatrixBase) != isinstance(rhs, sp.MatrixBase):
        return _result("fail", "structural", f"{what}: one side is a matrix and the other is not")
    if isinstance(lhs, sp.MatrixBase) and lhs.shape != rhs.shape:
        return _result("fail", "structural", f"{what}: shapes differ {lhs.shape} vs {rhs.shape}")
    diff = lhs - rhs
    if _is_zero_symbolically(diff):
        return _result("pass", "symbolic", f"{what}: proved symbolically (difference simplifies to 0)")
    outcome, info = numeric_compare(lhs, rhs, ctx)
    if outcome == "pass":
        return _result("pass", "numeric", f"{what}: agrees numerically at {info['valid_points']} random points "
                                          "(no symbolic proof found)", **info)
    if outcome == "fail":
        return _result("fail", "numeric", f"{what}: counterexample found", **info)
    return _result("inconclusive", "numeric", f"{what}: could not evaluate enough points", **info)


# ---------------------------------------------------------------------------------- check kinds
def check_identity(ctx: Context) -> dict:
    return compare(ctx.parse(ctx.spec["lhs"]), ctx.parse(ctx.spec["rhs"]), ctx, "identity")


def check_matrix_identity(ctx: Context) -> dict:
    lhs, rhs = ctx.parse(ctx.spec["lhs"]), ctx.parse(ctx.spec["rhs"])
    if not isinstance(lhs, sp.MatrixBase):
        lhs = sp.Matrix([[lhs]])
    if not isinstance(rhs, sp.MatrixBase):
        rhs = sp.Matrix([[rhs]])
    return compare(lhs, rhs, ctx, "matrix identity")


def check_derivative(ctx: Context) -> dict:
    expr = ctx.parse(ctx.spec["expr"])
    var = ctx.var(ctx.spec["var"])
    order = ctx.spec.get("order") or 1
    return compare(sp.diff(expr, var, order), ctx.parse(ctx.spec["expected"]), ctx, f"d^{order}/d{var}^{order}")


def check_gradient(ctx: Context) -> dict:
    expr = ctx.parse(ctx.spec["expr"])
    variables = ctx.vars(ctx.spec["var"])
    expected = ctx.spec["expected"]
    if isinstance(expected, str):
        expected_items = ctx.parse(expected)
        expected_list = list(expected_items) if isinstance(expected_items, (sp.MatrixBase, list, tuple)) else [expected_items]
    else:
        expected_list = [ctx.parse(e) for e in expected]
    if len(expected_list) != len(variables):
        raise SpecError(f"gradient: {len(variables)} variables but {len(expected_list)} expected components")
    worst = None
    for v, e in zip(variables, expected_list):
        res = compare(sp.diff(expr, v), e, ctx, f"d/d{v}")
        if res["outcome"] != "pass":
            res["message"] = f"gradient component ∂/∂{v}: " + res["message"]
            return res
        if worst is None or res["method"] == "numeric":
            worst = res
    worst["message"] = "gradient: all components " + ("proved symbolically" if worst["method"] == "symbolic"
                                                       else "agree numerically")
    return worst


def check_integral(ctx: Context) -> dict:
    expr = ctx.parse(ctx.spec["expr"])
    var = ctx.var(ctx.spec["var"])
    expected = ctx.parse(ctx.spec["expected"])
    lower, upper = ctx.spec.get("lower"), ctx.spec.get("upper")
    if lower is None and upper is None:
        # antiderivative: differentiate the claim (robust, avoids integrating)
        res = compare(sp.diff(expected, var), expr, ctx, "antiderivative (d/dx of claimed result equals integrand)")
        return res
    a, b = ctx.parse(lower), ctx.parse(upper)
    try:
        value = sp.integrate(expr, (var, a, b))
        if not value.has(sp.Integral):
            return compare(value, expected, ctx, "definite integral")
    except Exception:
        pass
    # numerical quadrature fallback when the integrand has no free parameters
    free = (expr.free_symbols - {var}) | expected.free_symbols
    if free:
        return _result("inconclusive", "numeric", "definite integral: no closed form and free parameters remain")
    f = sp.lambdify(var, expr, modules="mpmath")
    try:
        val = mpmath.quad(f, [mpmath.mpf(sp.N(a, 30)) if a.is_finite else (mpmath.inf if a > 0 else -mpmath.inf),
                              mpmath.mpf(sp.N(b, 30)) if b.is_finite else (mpmath.inf if b > 0 else -mpmath.inf)])
        exp_val = complex(sp.N(expected, 30))
        ok = _close(complex(val), exp_val, ctx.tol)
        return _result("pass" if ok else "fail", "numeric", "definite integral by numerical quadrature",
                       quadrature=str(val), expected=str(exp_val))
    except Exception as exc:
        return _result("inconclusive", "numeric", f"quadrature failed: {exc}")


def check_limit(ctx: Context) -> dict:
    expr = ctx.parse(ctx.spec["expr"])
    var = ctx.var(ctx.spec["var"])
    point = ctx.parse(ctx.spec["point"])
    expected = ctx.parse(ctx.spec["expected"])
    direction = ctx.spec.get("direction") or "+-"
    try:
        value = sp.limit(expr, var, point, direction) if direction != "+-" else sp.limit(expr, var, point)
        if not isinstance(value, sp.Limit):
            if value == expected or _is_zero_symbolically(value - expected):
                return _result("pass", "symbolic", f"limit computed symbolically: {value}")
            if value.is_number and expected.is_number:
                return _result("fail", "symbolic", f"limit is {value}, not {expected}", computed=str(value))
    except Exception:
        pass
    if expected.free_symbols or expr.free_symbols - {var}:
        return _result("inconclusive", "numeric", "limit: no symbolic result and free parameters remain")
    try:
        f = sp.lambdify(var, expr, modules="mpmath")
        p = complex(sp.N(point))
        vals = [complex(f(mpmath.mpf(p.real) + mpmath.mpf(10) ** -k)) for k in range(4, 9)]
        exp_val = complex(sp.N(expected))
        ok = abs(vals[-1] - exp_val) < 1e-4 * max(1, abs(exp_val))
        return _result("pass" if ok else "fail", "numeric", "limit estimated numerically from the right",
                       estimates=[str(v) for v in vals], expected=str(exp_val))
    except Exception as exc:
        return _result("inconclusive", "numeric", f"limit could not be evaluated: {exc}")


def check_series(ctx: Context) -> dict:
    expr = ctx.parse(ctx.spec["expr"])
    var = ctx.var(ctx.spec["var"])
    point = ctx.parse(ctx.spec["point"])
    order = int(ctx.spec["order"])
    expected = ctx.parse(ctx.spec["expected"])
    approx = sp.series(expr, var, point, order + 1).removeO()
    return compare(sp.expand(approx), sp.expand(expected), ctx, f"Taylor polynomial of order {order}")


def check_solve(ctx: Context) -> dict:
    lhs = ctx.parse(ctx.spec["lhs"])
    rhs = ctx.parse(ctx.spec["rhs"]) if ctx.spec.get("rhs") else sp.Integer(0)
    var = ctx.var(ctx.spec["var"])
    expected_raw = ctx.spec["expected"]
    expected = [ctx.parse(e) for e in (expected_raw if isinstance(expected_raw, list) else [expected_raw])]
    sols = sp.solve(sp.Eq(lhs, rhs), var, dict=False)
    sols = list(sols) if isinstance(sols, (list, tuple)) else [sols]
    unmatched_expected = [e for e in expected if not any(_is_zero_symbolically(e - s) for s in sols)]
    unmatched_found = [s for s in sols if not any(_is_zero_symbolically(e - s) for e in expected)]
    if not unmatched_expected and not unmatched_found:
        return _result("pass", "symbolic", f"solution set matches: {sols}")
    # verify each expected solution satisfies the equation at least
    satisfies = all(_is_zero_symbolically((lhs - rhs).subs(var, e)) for e in expected)
    return _result("fail", "symbolic", "solution set differs", found=[str(s) for s in sols],
                   expected=[str(e) for e in expected], expected_satisfy_equation=satisfies)


def check_inequality(ctx: Context) -> dict:
    rel = ctx.parse(ctx.spec["expr"])
    # SymPy may decide the relation while parsing, using the declared assumptions (e.g. exp(-x)/(...)**2 > 0)
    if rel is sp.true:
        return _result("pass", "symbolic", "inequality decided by SymPy's assumption system (always true)")
    if rel is sp.false:
        return _result("fail", "symbolic", "inequality decided by SymPy's assumption system (always false)")
    if not isinstance(rel, sp.Rel) or isinstance(rel, (sp.Eq, sp.Ne)):
        raise SpecError("inequality: 'expr' must be a strict or non-strict inequality, e.g. 'x**2 + 1 > 0'")
    free = sorted(rel.free_symbols, key=str)
    # symbolic attempt for univariate real inequalities
    if len(free) == 1 and free[0].is_real:
        x = free[0]
        domain_spec = (ctx.spec.get("domain") or {}).get(str(x))
        dom = sp.Interval(*domain_spec) if domain_spec else (sp.Interval(0, sp.oo) if x.is_nonnegative else sp.S.Reals)
        try:
            violations = sp.solveset(sp.Not(rel), x, domain=dom)
            if violations == sp.S.EmptySet:
                return _result("pass", "symbolic", f"inequality holds for all {x} in {dom} (proved by solveset)")
            if violations.is_empty is False:
                sample = None
                try:
                    if isinstance(violations, sp.Interval):
                        sample = str(violations.inf if violations.left_open is False else (violations.inf + violations.sup) / 2)
                    elif isinstance(violations, sp.FiniteSet):
                        sample = str(list(violations)[0])
                except Exception:
                    pass
                return _result("fail", "symbolic", f"inequality fails on {violations}", counterexample=sample)
        except Exception:
            pass
    # numeric falsification
    rng = random.Random(4321)
    domain = ctx.spec.get("domain") or {}
    valid = 0
    for _ in range(ctx.samples * 8):
        point = {s: _sample_value(s, domain, rng) for s in free}
        try:
            lhs_v, rhs_v = _to_number(rel.lhs.subs(point)), _to_number(rel.rhs.subs(point))
        except Exception:
            continue
        if lhs_v is None or rhs_v is None or abs(lhs_v.imag) > 1e-12 or abs(rhs_v.imag) > 1e-12:
            continue
        valid += 1
        a, b = lhs_v.real, rhs_v.real
        slack = 1e-12 * max(1.0, abs(a), abs(b))
        ok = {"<": a < b + slack, "<=": a <= b + slack, ">": a + slack > b, ">=": a + slack >= b}[rel.rel_op]
        if not ok:
            return _result("fail", "numeric", "counterexample to the inequality",
                           counterexample={str(k): str(v) for k, v in point.items()}, lhs=a, rhs=b)
        if valid >= ctx.samples * 4:
            break
    if valid < ctx.samples:
        return _result("inconclusive", "numeric", "too few valid sample points", valid_points=valid)
    return _result("pass", "numeric", f"no counterexample in {valid} random points (evidence, not proof)",
                   valid_points=valid)


def check_numeric(ctx: Context) -> dict:
    expr = ctx.parse(ctx.spec["expr"])
    expected = ctx.parse(ctx.spec["expected"])
    if expr.free_symbols or (hasattr(expected, "free_symbols") and expected.free_symbols):
        raise SpecError("numeric checks must not contain free symbols")
    if _is_zero_symbolically(expr - expected):
        return _result("pass", "symbolic", f"value equals {expected} exactly")
    a, b = _to_number(sp.N(expr, 40)), _to_number(sp.N(expected, 40))
    if a is None or b is None:
        return _result("inconclusive", "numeric", "could not evaluate the expression")
    tol = ctx.spec.get("tolerance") or 1e-9
    ok = abs(a - b) <= tol * max(1.0, abs(b))
    return _result("pass" if ok else "fail", "numeric", f"computed {a.real if a.imag == 0 else a}, expected {b}",
                   computed=str(a), expected=str(b), tolerance=tol)


def check_dimension(ctx: Context) -> dict:
    dims = {name: dimmod.parse_dimension(spec) for name, spec in (ctx.spec.get("dimensions") or {}).items()}
    lhs, rhs = ctx.parse(ctx.spec["lhs"]), ctx.parse(ctx.spec["rhs"])
    try:
        dl, dr = dimmod.dimension_of(lhs, dims), dimmod.dimension_of(rhs, dims)
    except dimmod.DimensionError as exc:
        return _result("fail", "dimensional", str(exc))
    if dimmod.same_dimension(dl, dr):
        return _result("pass", "dimensional", f"both sides have dimension {dimmod.describe(dl)}")
    return _result("fail", "dimensional", f"dimension mismatch: lhs {dimmod.describe(dl)} vs rhs {dimmod.describe(dr)}")


KINDS = {
    "identity": check_identity,
    "matrix_identity": check_matrix_identity,
    "derivative": check_derivative,
    "gradient": check_gradient,
    "integral": check_integral,
    "limit": check_limit,
    "series": check_series,
    "solve": check_solve,
    "inequality": check_inequality,
    "numeric": check_numeric,
    "dimension": check_dimension,
}


def run_check(spec: dict[str, Any], samples: int = DEFAULT_SAMPLES, tol: float = DEFAULT_TOL) -> dict[str, Any]:
    kind = spec.get("kind")
    fn = KINDS.get(kind)
    base = {"id": spec.get("id"), "kind": kind}
    if fn is None:
        return base | _result("error", "none", f"unsupported check kind {kind!r} in worker")
    try:
        ctx = Context(spec, samples, tol)
        return base | fn(ctx)
    except (SpecError, UnsafeExpression, dimmod.DimensionError, ValueError, TypeError) as exc:
        return base | _result("error", "none", f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # SymPy internals may raise anything on odd input
        return base | _result("error", "none", f"unexpected {type(exc).__name__}: {exc}")
