import pytest

from scholarforge.science.mathcheck import MathCheckRunner
from scholarforge.science.mathcheck.safe_parse import UnsafeExpression, safe_parse, validate_tokens


@pytest.fixture(scope="module")
def runner():
    return MathCheckRunner(timeout_s=40)


def run(runner, **spec):
    spec.setdefault("id", "c1")
    return runner.run([spec])[0]


def test_symbolic_identity_and_derivative(runner):
    r = run(runner, kind="derivative", expr="1/(1+exp(-x))", var="x", expected="(1/(1+exp(-x)))*(1-1/(1+exp(-x)))")
    assert (r.outcome, r.method) == ("pass", "symbolic") and r.independent_proof


def test_wrong_derivative_gives_counterexample(runner):
    r = run(runner, kind="derivative", expr="x**3", var="x", expected="3*x")
    assert r.outcome == "fail" and "counterexample" in r.details


def test_batch_of_kinds(runner):
    specs = [
        {"id": "grad", "kind": "gradient", "expr": "x**2*y + sin(y)", "var": ["x", "y"],
         "expected": ["2*x*y", "x**2 + cos(y)"]},
        {"id": "anti", "kind": "integral", "expr": "x*exp(x)", "var": "x", "expected": "(x-1)*exp(x)"},
        {"id": "gauss", "kind": "integral", "expr": "exp(-x**2)", "var": "x", "lower": "-oo", "upper": "oo",
         "expected": "sqrt(pi)"},
        {"id": "lim", "kind": "limit", "expr": "sin(x)/x", "var": "x", "point": "0", "expected": "1"},
        {"id": "taylor", "kind": "series", "expr": "exp(x)", "var": "x", "point": "0", "order": 3,
         "expected": "1 + x + x**2/2 + x**3/6"},
        {"id": "mat", "kind": "matrix_identity", "lhs": "transpose(A*B)", "rhs": "transpose(B)*transpose(A)",
         "matrices": {"A": [2, 3], "B": [3, 2]}},
        {"id": "solve", "kind": "solve", "lhs": "x**2 - 4", "var": "x", "expected": ["2", "-2"]},
        {"id": "num", "kind": "numeric", "expr": "integrate(x**2, (x, 0, 1))", "expected": "1/3"},
        {"id": "ineq", "kind": "inequality", "expr": "x**2 - 2*x + 1 >= 0", "symbols": {"x": {"real": True}}},
        {"id": "ineq_true", "kind": "inequality", "expr": "exp(-x)/(1 + exp(-x))**2 > 0",
         "symbols": {"x": {"real": True}}},
        {"id": "ineq_bad", "kind": "inequality", "expr": "x**2 - 3*x + 1 >= 0", "symbols": {"x": {"real": True}}},
    ]
    res = {r.id: r for r in runner.run(specs)}
    for key in ("grad", "anti", "gauss", "lim", "taylor", "mat", "solve", "num", "ineq", "ineq_true"):
        assert res[key].outcome == "pass", (key, res[key].message)
    assert res["ineq_bad"].outcome == "fail"


def test_dimension_checks_and_energy_symbol(runner):
    ok = run(runner, kind="dimension", lhs="E", rhs="m*c**2",
             dimensions={"E": "energy", "m": "mass", "c": "velocity"})
    bad = run(runner, kind="dimension", lhs="E", rhs="m*c",
              dimensions={"E": "energy", "m": "mass", "c": "velocity"})
    arg = run(runner, kind="dimension", lhs="x", rhs="exp(x)", dimensions={"x": "length"})
    assert ok.outcome == "pass" and ok.method == "dimensional"
    assert bad.outcome == "fail" and "mismatch" in bad.message
    assert arg.outcome == "fail" and "dimensionless" in arg.message


@pytest.mark.parametrize("expr", [
    "().__class__", "__import__('os')", "eval('1')", "open('x')", "lambda x: x", "x.real", "'abc'",
    "getattr(x, 'y')", "x; y", "9" * 30,
])
def test_unsafe_expressions_rejected(expr):
    with pytest.raises(UnsafeExpression):
        validate_tokens(expr)


def test_safe_parse_supports_math_names():
    import sympy as sp

    e = safe_parse("sigmoid(x) + ln(x) + abs(x)", {"x": sp.Symbol("x", positive=True)})
    assert e.has(sp.exp) and e.has(sp.log)
    with pytest.raises(UnsafeExpression):
        safe_parse("x = 1")


def test_worker_reports_errors_not_crashes(runner):
    r = run(runner, kind="identity", lhs="__import__('os').system('echo pwned')", rhs="0")
    assert r.outcome == "error" and "not allowed" in r.message


def test_timeout_is_isolated(tmp_path):
    slow = MathCheckRunner(timeout_s=3)
    res = slow.run([{"id": "slow", "kind": "numeric", "expr": "factorial(10**7)", "expected": "1"},
                    {"id": "fast", "kind": "identity", "lhs": "x + x", "rhs": "2*x"}])
    by = {r.id: r for r in res}
    assert by["fast"].outcome == "pass"
    assert by["slow"].outcome in ("inconclusive", "error", "fail")


def test_lean_missing_is_skipped(runner):
    r = run(runner, kind="lean", code="theorem t : 1 + 1 = 2 := rfl")
    assert r.outcome in ("skipped", "pass")
    sorry = run(runner, kind="lean", code="theorem t : 1 + 1 = 2 := by sorry")
    assert sorry.outcome == "inconclusive"
