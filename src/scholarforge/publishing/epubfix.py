"""Post-processing of EPUB files produced by Quarto/Pandoc.

Quarto copies a figure's ``fig-alt`` onto the wrapping ``<div class="quarto-float">``
as an ``alt`` attribute, which is invalid XHTML and makes epubcheck fail (RSC-005).
The image itself keeps its correct ``alt`` text, so the attribute is removed from
every element that may not carry it. The container is rewritten with ``mimetype``
first and uncompressed, as OCF requires.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path

_ALT_ALLOWED = ("img", "area", "input")
_TAG = re.compile(r"<(?P<name>[a-zA-Z][\w:-]*)(?P<attrs>\s[^<>]*?)?(?P<end>/?)>")


def _strip_invalid_alt(xhtml: str) -> tuple[str, int]:
    count = 0

    def fix(m: re.Match) -> str:
        nonlocal count
        name, attrs = m.group("name"), m.group("attrs") or ""
        if name.lower() in _ALT_ALLOWED or " alt=" not in attrs:
            return m.group(0)
        new_attrs, n = re.subn(r'\salt="[^"]*"', "", attrs)
        count += n
        return f"<{name}{new_attrs}{m.group('end')}>"

    return _TAG.sub(fix, xhtml), count


def fix_epub(path: Path) -> dict[str, int]:
    path = Path(path)
    stats = {"files_changed": 0, "attributes_removed": 0}
    tmp_dir = Path(tempfile.mkdtemp(prefix="sf-epub-"))
    out = tmp_dir / path.name
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(out, "w") as dst:
        dst.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for info in src.infolist():
            if info.filename == "mimetype":
                continue
            data = src.read(info.filename)
            if info.filename.endswith((".xhtml", ".html")):
                text, n = _strip_invalid_alt(data.decode("utf-8"))
                if n:
                    stats["files_changed"] += 1
                    stats["attributes_removed"] += n
                    data = text.encode("utf-8")
            dst.writestr(info.filename, data, compress_type=zipfile.ZIP_DEFLATED)
    shutil.move(str(out), str(path))
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return stats
