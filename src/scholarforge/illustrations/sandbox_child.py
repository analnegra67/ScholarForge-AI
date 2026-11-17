"""Child process that executes AI-written plotting code (sandbox layer 2).

Started by :mod:`scholarforge.illustrations.sandbox` with ``python -I``, a scrubbed
environment, a temporary working directory and a timeout (plus a memory limit
where the OS supports it). Allowed libraries are imported *before* a PEP 578
audit hook is installed; afterwards the hook refuses network access, process
creation, ctypes, file writes outside the output directory, reads outside the
Python/matplotlib/font installation, and nested exec/compile.

This is defence in depth, not a virtual machine: the policy is documented and
the user can disable native rendering entirely.
"""

from __future__ import annotations

import json
import os
import sys


def _prepare(outdir: str):
    import math  # noqa: F401
    import statistics  # noqa: F401

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib import (cm, collections, colors, gridspec, lines, markers, patches, path, patheffects,  # noqa: F401
                            ticker, transforms)
    from matplotlib.backends import backend_agg, backend_pdf, backend_svg  # noqa: F401
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

    try:
        import scipy  # noqa: F401
        import scipy.integrate  # noqa: F401
        import scipy.interpolate  # noqa: F401
        import scipy.linalg  # noqa: F401
        import scipy.optimize  # noqa: F401
        import scipy.special  # noqa: F401
        import scipy.stats  # noqa: F401
    except ImportError:
        pass
    import PIL.PngImagePlugin  # noqa: F401

    plt.rcParams.update({
        "figure.figsize": (6.4, 4.0), "figure.dpi": 100, "savefig.bbox": "tight", "savefig.pad_inches": 0.04,
        "font.family": "serif", "font.serif": ["DejaVu Serif"], "mathtext.fontset": "dejavuserif",
        "font.size": 10, "axes.titlesize": 10, "axes.labelsize": 10, "legend.fontsize": 9,
        "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "path", "axes.grid": False,
        "lines.linewidth": 1.4, "image.cmap": "viridis",
    })
    # Warm up text, font and every output backend (incl. fontTools subsetting, which builds classes
    # dynamically) so that lazy imports and file reads happen before the audit hook is armed.
    import io

    fig = plt.figure()
    ax = fig.add_subplot(111)
    ax.plot([0, 1], [0, 1], label="line")
    ax.set_title("warm-up éßЖα $\\alpha^2 + \\int_0^1 x\\,dx$")
    ax.legend()
    for fmt in ("pdf", "svg", "png"):
        fig.savefig(io.BytesIO(), format=fmt)
    plt.close(fig)
    return plt, np, matplotlib


def _install_hook(outdir: str, read_roots: list[str]) -> None:
    out_real = os.path.realpath(outdir)
    roots = [os.path.realpath(r) for r in read_roots if r]
    # Libraries that legitimately compile small internal expressions at run time (fontTools builds
    # table converters while subsetting fonts for PDF output). Only calls *made from their own files*
    # are allowed; user code can neither import them nor pass strings to them.
    dynamic_ok: list[str] = []
    try:
        import fontTools

        dynamic_ok.append(os.path.realpath(os.path.dirname(fontTools.__file__)))
    except ImportError:
        pass
    denied_imports = {"subprocess", "socket", "ssl", "http", "urllib", "ftplib", "smtplib", "ctypes",
                      "multiprocessing", "pty", "webbrowser", "requests", "httpx", "asyncio", "shutil", "pickle",
                      "marshal", "importlib", "code", "codeop", "runpy", "sqlite3", "tempfile"}
    state = {"exec_count": 0}

    def inside(path: str, base: str) -> bool:
        try:
            return os.path.commonpath([os.path.realpath(path), base]) == base
        except ValueError:
            return False

    def hook(event: str, args: tuple) -> None:
        if event == "open":
            path, mode = args[0], args[1] if len(args) > 1 else "r"
            if isinstance(path, int):
                return
            path = os.fsdecode(path)
            writing = isinstance(mode, str) and any(c in mode for c in "wax+")
            if isinstance(mode, int):  # os.open flags
                writing = bool(mode & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC))
            if writing:
                if not inside(path, out_real):
                    raise PermissionError(f"sandbox: writing outside the output directory is not allowed ({path})")
                return
            if inside(path, out_real) or any(inside(path, r) for r in roots):
                return
            raise PermissionError(f"sandbox: reading {path} is not allowed")
        if event.startswith(("socket.", "subprocess.", "ctypes.", "winreg.", "urllib.", "http.", "ftplib.",
                             "smtplib.", "webbrowser.", "os.exec", "os.spawn", "os.posix_spawn", "os.fork",
                             "os.kill", "os.startfile", "os.system", "shutil.")):
            raise PermissionError(f"sandbox: '{event}' is not allowed")
        if event in ("os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod", "os.symlink", "os.link",
                     "os.truncate", "os.utime"):
            target = os.fsdecode(args[0]) if args and not isinstance(args[0], int) else ""
            if not target or not inside(target, out_real):
                raise PermissionError(f"sandbox: '{event}' outside the output directory is not allowed")
        if event == "import" and args and isinstance(args[0], str):
            top = args[0].split(".")[0]
            if top in denied_imports:
                raise PermissionError(f"sandbox: importing '{args[0]}' is not allowed")
        if event in ("exec", "compile"):
            caller = sys._getframe(1).f_code.co_filename  # the frame that invoked exec/eval/compile
            if caller and any(inside(caller, d) for d in dynamic_ok):
                return
        if event == "exec":
            # Imports execute module code objects too: allow code that comes from installed library files,
            # allow the user's figure code exactly once, refuse code built from strings at runtime.
            filename = getattr(args[0], "co_filename", "") if args else ""
            if filename == "<figure-code>":
                state["exec_count"] += 1
                if state["exec_count"] > 1:
                    raise PermissionError("sandbox: re-executing the figure code is not allowed")
                return
            if filename and os.path.isabs(filename) and any(inside(filename, r) for r in roots):
                return
            raise PermissionError("sandbox: dynamic code execution is not allowed")
        if event == "compile":
            filename = args[1] if len(args) > 1 else None
            filename = os.fsdecode(filename) if isinstance(filename, (str, bytes)) else ""
            if filename and os.path.isabs(filename) and any(inside(filename, r) for r in roots):
                return
            raise PermissionError("sandbox: compiling code at runtime is not allowed")
        if event == "sys.addaudithook" or event == "sys.setprofile" or event == "sys.settrace":
            raise PermissionError(f"sandbox: '{event}' is not allowed")

    sys.addaudithook(hook)


SAFE_BUILTINS = [
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float", "format", "frozenset", "hash",
    "int", "isinstance", "issubclass", "iter", "len", "list", "map", "max", "min", "next", "pow", "print", "range",
    "repr", "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple", "zip", "complex", "chr", "ord",
    "ValueError", "TypeError", "ZeroDivisionError", "ArithmeticError", "Exception", "IndexError", "KeyError",
    "RuntimeError", "StopIteration", "True", "False", "None", "NotImplemented", "callable", "id", "bin", "hex",
    "oct", "ascii", "bytes",
]


def main() -> int:
    request = json.loads(sys.stdin.read())
    outdir = request["outdir"]
    code = request["code"]
    width_in = float(request.get("width_in", 6.0))
    plt, np, matplotlib = _prepare(outdir)
    import builtins
    import importlib

    allowed_modules = set(request["allowed_modules"])

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name not in allowed_modules:
            raise ImportError(f"sandbox: import of '{name}' is not allowed")
        # same semantics as the builtin (``import a.b`` returns package ``a``), restricted to the allow-list
        return real_import(name, globals, locals, fromlist, 0)

    # resolve modules once, before the hook blocks importlib
    for mod in sorted(allowed_modules):
        try:
            importlib.import_module(mod)
        except Exception:
            pass

    safe_builtins = {name: getattr(builtins, name) for name in SAFE_BUILTINS if hasattr(builtins, name)}
    safe_builtins["__import__"] = guarded_import
    safe_builtins["__build_class__"] = builtins.__build_class__  # plain classes; dataclasses still need exec
    printed: list[str] = []
    safe_builtins["print"] = lambda *a, **k: printed.append(" ".join(str(x) for x in a)[:500])
    env = {"__builtins__": safe_builtins, "__name__": "__figure__", "np": np, "plt": plt, "matplotlib": matplotlib}
    compiled = compile(code, "<figure-code>", "exec")
    _install_hook(outdir, request.get("read_roots", []))
    try:
        exec(compiled, env)  # noqa: S102 - the whole point of the sandbox
        fig = env.get("fig")
        if fig is None or not hasattr(fig, "savefig"):
            fig = plt.gcf()
        if not fig.get_axes() and not fig.texts and not fig.patches:
            raise RuntimeError("the code produced an empty figure")
        w, h = fig.get_size_inches()
        if w > 0:
            fig.set_size_inches(width_in, h * width_in / w)
        fig.savefig(os.path.join(outdir, "figure.pdf"))
        fig.savefig(os.path.join(outdir, "figure.svg"))
        fig.savefig(os.path.join(outdir, "figure.png"), dpi=int(request.get("dpi", 400)))
        meta = {"size_in": [float(x) for x in fig.get_size_inches()], "axes": len(fig.get_axes()),
                "printed": printed[:20], "matplotlib": matplotlib.__version__, "numpy": np.__version__}
        with open(os.path.join(outdir, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh)
    except BaseException as exc:  # report every failure to the parent
        # (no `traceback` module here: formatting it compiles source, which the audit hook refuses)
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        frames = []
        tb = exc.__traceback__
        while tb is not None:
            if tb.tb_frame.f_code.co_filename == "<figure-code>":
                frames.append(tb.tb_lineno)
            tb = tb.tb_next
        if frames:
            lines = code.splitlines()
            for ln in frames[-3:]:
                text = lines[ln - 1].strip() if 0 < ln <= len(lines) else ""
                sys.stderr.write(f"  at figure code line {ln}: {text}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
