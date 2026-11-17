"""Dimensional analysis on SymPy expression trees (SI base dimensions).

Each symbol is given a dimension such as ``mass``, ``energy``, ``length/time`` or
``mass*length**2/time**2``. The walker enforces the usual rules: terms of a sum
share one dimension, arguments of transcendental functions are dimensionless,
exponents are dimensionless numbers, derivatives/integrals divide/multiply by the
variable's dimension.
"""

from __future__ import annotations

import sympy as sp
from sympy.physics.units.definitions import dimension_definitions as dd
from sympy.physics.units.dimensions import Dimension
from sympy.physics.units.systems.si import dimsys_SI

BASE = {
    "length": dd.length, "mass": dd.mass, "time": dd.time, "current": dd.current, "temperature": dd.temperature,
    "amount": dd.amount_of_substance, "amount_of_substance": dd.amount_of_substance,
    "luminous_intensity": dd.luminous_intensity, "information": dd.information,
}
DERIVED = {
    "velocity": dd.velocity, "acceleration": dd.acceleration, "momentum": dd.momentum, "force": dd.force,
    "energy": dd.energy, "power": dd.power, "pressure": dd.pressure, "frequency": dd.frequency,
    "action": dd.action, "area": dd.area, "volume": dd.volume, "charge": dd.charge, "voltage": dd.voltage,
    "capacitance": dd.capacitance, "resistance": dd.impedance, "impedance": dd.impedance,
    "magnetic_flux": dd.magnetic_flux, "magnetic_density": dd.magnetic_density, "inductance": dd.inductance,
    "conductance": dd.conductance,
}
DIMENSIONLESS = Dimension(1)


class DimensionError(ValueError):
    pass


def parse_dimension(spec: str) -> Dimension:
    spec = (spec or "").strip()
    if spec in ("", "1", "dimensionless", "none"):
        return DIMENSIONLESS
    from scholarforge.science.mathcheck.safe_parse import validate_tokens

    validate_tokens(spec)
    names = {**BASE, **DERIVED}
    local = {n: sp.Symbol(n) for n in names}
    try:
        expr = sp.sympify(spec, locals=local)  # tokens validated above; only dimension names possible
    except Exception as exc:
        raise DimensionError(f"cannot parse dimension {spec!r}: {exc}") from exc
    unknown = [str(s) for s in expr.free_symbols if str(s) not in names]
    if unknown:
        raise DimensionError(f"unknown dimension names {unknown}; known: {sorted(names)}")
    subs = {local[n]: names[n].name for n in names}
    dim_expr = expr.subs(subs)
    return Dimension(dim_expr)


def _deps(d: Dimension) -> dict:
    return dict(dimsys_SI.get_dimensional_dependencies(d, mark_dimensionless=False))


def same_dimension(a: Dimension, b: Dimension) -> bool:
    return _deps(a) == _deps(b)


def is_dimensionless(d: Dimension) -> bool:
    return _deps(d) == {}


def dimension_of(expr: sp.Basic, dims: dict[str, Dimension]) -> Dimension:
    if expr.is_Number or expr in (sp.pi, sp.E, sp.I):
        return DIMENSIONLESS
    if expr.is_Symbol:
        name = str(expr)
        if name not in dims:
            raise DimensionError(f"no dimension declared for symbol '{name}'")
        return dims[name]
    if isinstance(expr, sp.Add):
        parts = [dimension_of(a, dims) for a in expr.args]
        first = parts[0]
        for term, d in zip(expr.args[1:], parts[1:]):
            if not same_dimension(first, d):
                raise DimensionError(f"adding quantities of different dimensions: '{expr.args[0]}' "
                                     f"({_fmt(first)}) and '{term}' ({_fmt(d)})")
        return first
    if isinstance(expr, sp.Mul):
        out = DIMENSIONLESS
        for a in expr.args:
            out = out * dimension_of(a, dims)
        return out
    if isinstance(expr, sp.Pow):
        base_d = dimension_of(expr.base, dims)
        exp_d = dimension_of(expr.exp, dims)
        if not is_dimensionless(exp_d):
            raise DimensionError(f"exponent '{expr.exp}' is not dimensionless")
        if is_dimensionless(base_d):
            return DIMENSIONLESS
        if not expr.exp.is_number:
            raise DimensionError(f"symbolic exponent '{expr.exp}' applied to dimensional base '{expr.base}'")
        return Dimension(base_d.name ** expr.exp)
    if isinstance(expr, sp.Derivative):
        d = dimension_of(expr.expr, dims)
        for var, count in expr.variable_count:
            d = Dimension(d.name / dimension_of(var, dims).name ** count)
        return d
    if isinstance(expr, sp.Integral):
        d = dimension_of(expr.function, dims)
        for lim in expr.limits:
            d = Dimension(d.name * dimension_of(lim[0], dims).name)
        return d
    if isinstance(expr, sp.Abs):
        return dimension_of(expr.args[0], dims)
    if isinstance(expr, (sp.Max, sp.Min)):
        parts = [dimension_of(a, dims) for a in expr.args]
        for d in parts[1:]:
            if not same_dimension(parts[0], d):
                raise DimensionError(f"Max/Min of different dimensions in '{expr}'")
        return parts[0]
    if isinstance(expr, sp.Function):
        for a in expr.args:
            if not is_dimensionless(dimension_of(a, dims)):
                raise DimensionError(f"argument of {expr.func.__name__}() must be dimensionless: '{a}'")
        return DIMENSIONLESS
    if isinstance(expr, sp.Relational):
        l, r = dimension_of(expr.lhs, dims), dimension_of(expr.rhs, dims)
        if not same_dimension(l, r):
            raise DimensionError("relation between different dimensions")
        return l
    raise DimensionError(f"unsupported construct for dimensional analysis: {type(expr).__name__}")


def _fmt(d: Dimension) -> str:
    deps = _deps(d)
    if not deps:
        return "dimensionless"
    return "*".join(f"{k.name if hasattr(k, 'name') else k}^{v}" for k, v in sorted(deps.items(), key=lambda kv: str(kv[0])))


def describe(d: Dimension) -> str:
    return _fmt(d)
