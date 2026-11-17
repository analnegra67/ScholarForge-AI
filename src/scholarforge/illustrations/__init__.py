"""Illustration engine.

Mode A (native, preferred for scientific content): the host AI writes plotting code
(Matplotlib), DOT (Graphviz), TikZ, SVG or Mermaid; ScholarForge renders it locally
in a sandbox, producing vector PDF/SVG and high-resolution PNG.

Mode B (external, optional, paid by the user): image generation through the
official OpenAI Images API with the user's own key, after an explicit cost
confirmation.
"""
