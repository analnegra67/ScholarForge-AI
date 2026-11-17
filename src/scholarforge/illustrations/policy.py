"""Static policy for AI-generated plotting code (sandbox layer 1: AST allow-list)."""

from __future__ import annotations

import ast
from dataclasses import dataclass

ALLOWED_MODULES = {
    "numpy", "math", "cmath", "random", "statistics", "fractions", "decimal", "itertools", "functools",
    "collections", "dataclasses", "typing", "string", "textwrap",
    "matplotlib", "matplotlib.pyplot", "matplotlib.patches", "matplotlib.lines", "matplotlib.colors",
    "matplotlib.cm", "matplotlib.ticker", "matplotlib.collections", "matplotlib.path", "matplotlib.transforms",
    "matplotlib.gridspec", "matplotlib.patheffects", "matplotlib.markers", "matplotlib.font_manager",
    "mpl_toolkits", "mpl_toolkits.mplot3d", "mpl_toolkits.mplot3d.art3d", "mpl_toolkits.axes_grid1",
    "scipy", "scipy.special", "scipy.stats", "scipy.integrate", "scipy.optimize", "scipy.linalg",
    "scipy.interpolate", "scipy.signal", "scipy.spatial", "scipy.ndimage",
}
# SymPy is deliberately absent: many SymPy functions evaluate strings (sympify). Symbolic work belongs in
# math_checks, which run in their own token-validated sandbox; figures use NumPy.
FORBIDDEN_NAMES = {
    "open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr", "delattr",
    "input", "breakpoint", "exit", "quit", "memoryview", "help", "dir", "type", "object", "super", "classmethod",
    "staticmethod", "property", "__builtins__", "sympify", "parse_expr", "lambdify", "S",
}
FORBIDDEN_ATTRS = {
    # file I/O and code execution reachable from the allowed libraries
    "savefig", "show", "switch_backend", "use", "imread", "imsave", "load", "loads", "save", "savez",
    "savez_compressed", "fromfile", "tofile", "loadtxt", "savetxt", "genfromtxt", "memmap", "ctypeslib",
    "DataSource", "lib", "system", "popen", "remove", "unlink", "rmdir", "rename", "chdir", "environ", "getenv",
    "preview", "lambdify", "sympify", "parse_expr", "ctypes", "dump", "dumps", "open", "read_csv", "to_csv",
    "testing", "f2py", "distutils", "rc_file", "rcParams_orig", "set_loglevel", "get_cachedir",
    "get_data_path", "matplotlib_fname", "font_manager_fname",
}
MAX_CODE_CHARS = 60000


@dataclass
class PolicyViolation:
    line: int
    message: str


class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[PolicyViolation] = []

    def _bad(self, node: ast.AST, msg: str) -> None:
        self.violations.append(PolicyViolation(getattr(node, "lineno", 0), msg))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name not in ALLOWED_MODULES:
                self._bad(node, f"import of '{alias.name}' is not allowed")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        if node.level or mod not in ALLOWED_MODULES:
            self._bad(node, f"import from '{mod or '.'}' is not allowed")
        for alias in node.names:
            if alias.name == "*":
                self._bad(node, "star imports are not allowed")
            elif alias.name.startswith("_") or alias.name in FORBIDDEN_NAMES or alias.name in FORBIDDEN_ATTRS:
                self._bad(node, f"importing '{alias.name}' is not allowed")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_NAMES or node.id.startswith("__"):
            self._bad(node, f"name '{node.id}' is not allowed")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            self._bad(node, f"access to private attribute '.{node.attr}' is not allowed")
        elif node.attr in FORBIDDEN_ATTRS:
            self._bad(node, f"attribute '.{node.attr}' is not allowed (file/system access); the figure is saved "
                            "automatically")
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self._bad(node, "global statements are not allowed")

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self._bad(node, "nonlocal statements are not allowed")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._bad(node, "async code is not allowed")

    def visit_Await(self, node: ast.Await) -> None:
        self._bad(node, "async code is not allowed")


def check_python(code: str) -> list[PolicyViolation]:
    if len(code) > MAX_CODE_CHARS:
        return [PolicyViolation(0, f"code longer than {MAX_CODE_CHARS} characters")]
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return [PolicyViolation(exc.lineno or 0, f"syntax error: {exc.msg}")]
    v = _Visitor()
    v.visit(tree)
    return v.violations
