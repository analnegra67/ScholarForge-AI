"""Machine verification of mathematical assertions (SymPy, NumPy/mpmath, dimensions, optional Lean 4).

Untrusted expressions are validated token-by-token (no attribute access, strings,
keywords or dunder names) and evaluated only inside a separate worker process with
a timeout. A passing *symbolic* or *formal* check is independent evidence; a
numerical agreement is reported as such and never as a proof.
"""

from scholarforge.science.mathcheck.runner import MathCheckRunner, MathResult

__all__ = ["MathCheckRunner", "MathResult"]
