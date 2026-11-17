"""Images supplied by the user (photos, scans, third-party figures) with license provenance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from scholarforge.errors import InvalidInput
from scholarforge.illustrations.engine import text_width_in
from scholarforge.illustrations.qc import check_figure
from scholarforge.models.enums import FigureStatus
from scholarforge.models.results import LABEL_RE
from scholarforge.util.atomic import atomic_write_bytes, atomic_write_json
from scholarforge.util.hashing import sha256_file
from scholarforge.util.timeutil import now_iso

ALLOWED = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".pdf"}
MAX_BYTES = 80 * 1024 * 1024


def import_image(project, *, label: str, file_path: Path, caption: str, alt_text: str, license: str,
                 source: str | None, block_id: str | None, width: float = 0.85) -> dict[str, Any]:
    if not LABEL_RE.match(label) or not label.startswith("fig-"):
        raise InvalidInput("label must look like fig-name")
    src = Path(file_path).expanduser().resolve()
    if not src.is_file():
        raise InvalidInput(f"file not found: {src}")
    if src.suffix.lower() not in ALLOWED:
        raise InvalidInput(f"unsupported image type {src.suffix}; allowed: {sorted(ALLOWED)}")
    if src.stat().st_size > MAX_BYTES:
        raise InvalidInput("image larger than 80 MB")
    if not license.strip():
        raise InvalidInput("state the license or permission for this image")
    out_dir = project.figures_dir / label
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Path] = {}
    if src.suffix.lower() == ".pdf":
        atomic_write_bytes(out_dir / "figure.pdf", src.read_bytes())
        from scholarforge.illustrations.engine import _pdf_to_png

        _pdf_to_png(out_dir / "figure.pdf", out_dir / "figure.png", dpi=300)
        outputs = {"pdf": out_dir / "figure.pdf", "png": out_dir / "figure.png"}
    else:
        with Image.open(src) as im:
            im.load()
            rgb = im.convert("RGB") if im.mode not in ("RGB", "L") else im.copy()
        rgb.save(out_dir / "figure.png", dpi=(300, 300))
        rgb.save(out_dir / "figure.pdf", "PDF", resolution=300.0)
        outputs = {"png": out_dir / "figure.png", "pdf": out_dir / "figure.pdf"}
    provenance = {"mode": "imported", "original_file": src.name, "original_sha256": sha256_file(src),
                  "license": license, "source": source, "imported_at": now_iso()}
    atomic_write_json(out_dir / "provenance.json", provenance)
    qc, problems = check_figure(outputs, caption=caption, alt_text=alt_text, width_fraction=width,
                                text_width_in=text_width_in(project), min_ppi=project.config.print.min_image_ppi,
                                print_color=project.config.print.color)
    fig = project.db.figures.upsert(label=label, node_id=block_id, mode="imported", renderer="file",
                                    spec={"renderer": "file", "width": width}, caption=caption, alt_text=alt_text,
                                    license=license)
    project.db.figures.update(fig.id, status=FigureStatus.RENDERED, provenance=provenance, qc=qc,
                              outputs={k: v.relative_to(project.root).as_posix() for k, v in outputs.items()})
    return {"label": label, "outputs": {k: str(v) for k, v in outputs.items()}, "qc_problems": problems}
