"""Build orchestration: assemble -> render each format -> post-process -> preflight -> export.

Every build is reproducible from stored sources (no model calls). Formats are
built independently: a failure in one (e.g. a TeX problem) never prevents the
others, and never touches the book sources.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pikepdf

from scholarforge.errors import WorkflowError
from scholarforge.logging_setup import get_logger
from scholarforge.publishing import latex
from scholarforge.publishing.assemble import BookAssembler
from scholarforge.publishing.cover import build_cover, cover_geometry
from scholarforge.publishing.pdfx import OutputIntent, finalize_print_pdf
from scholarforge.publishing.preflight import check_epub, check_html_site, preflight_pdf
from scholarforge.publishing.quarto import build_workdir, copy_tree, render
from scholarforge.tools.detect import epubcheck_jar, find_tool, tex_find_file
from scholarforge.util.atomic import atomic_write_json, atomic_write_text
from scholarforge.util.timeutil import now_iso

log = get_logger("builder")

SCREEN_FORMATS = ("html", "epub", "pdf")


class BuildLock:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self) -> "BuildLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                pid = int(self.path.read_text().split()[0])
            except (ValueError, IndexError):
                pid = -1
            if pid > 0 and _pid_alive(pid) and pid != os.getpid():
                raise WorkflowError(f"another build is running (pid {pid})", hint="wait for it or delete the lock file")
            self.path.unlink(missing_ok=True)
        self.path.write_text(f"{os.getpid()} {now_iso()}")
        return self

    def __exit__(self, *exc) -> None:
        self.path.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def resolve_output_intent(project) -> OutputIntent | None:
    setting = project.config.print.icc_profile
    if setting and setting != "auto":
        p = Path(setting).expanduser()
        if not p.is_absolute():
            p = project.root / p
        return OutputIntent(icc_path=p, identifier=p.stem, info=p.stem) if p.exists() else None
    found = tex_find_file("FOGRA39L_coated.icc")
    if not found:
        tlmgr = find_tool("tlmgr")
        if tlmgr:
            subprocess.run([tlmgr, "install", "colorprofiles"], capture_output=True, timeout=900)
            found = tex_find_file("FOGRA39L_coated.icc")
    if not found:
        return None
    return OutputIntent(icc_path=Path(found), identifier="FOGRA39",
                        info="Coated FOGRA39 (ISO 12647-2:2004), 300% TAC (FOGRA39L)")


class BookBuilder:
    def __init__(self, project):
        self.project = project
        self.pdb = project.db
        self.settings = project.settings
        self.logs = project.build_dir / "logs"

    # ------------------------------------------------------------------------------------- api
    def build(self, formats: list[str] | None = None, clean: bool = False) -> dict[str, Any]:
        wanted = list(formats or self.project.config.params.formats)
        for f in wanted:
            if f not in ("html", "epub", "pdf", "print", "latex", "quarto"):
                raise WorkflowError(f"unknown format '{f}'")
        with BuildLock(self.project.state_dir / "locks" / "build.lock"):
            return self._build(wanted, clean)

    def _build(self, wanted: list[str], clean: bool) -> dict[str, Any]:
        started = time.monotonic()
        build_id = self.pdb.builds.start(wanted)
        edits = self.project.content.detect_external_edits()
        base = build_workdir(self.project.id)
        if clean and base.exists():
            shutil.rmtree(base, ignore_errors=True)
            base.mkdir(parents=True)
        results: dict[str, Any] = {}
        preflight: dict[str, Any] = {}
        screen_needed = any(f in wanted for f in ("html", "epub", "pdf", "latex", "quarto"))
        exports = self.project.exports_dir
        if screen_needed:
            work = base / "screen"
            assembled = BookAssembler(self.project, edition="screen").assemble(work)
            results["assembly"] = {"chapters": len(assembled.chapters), "appendices": len(assembled.appendices),
                                   "figures": len(assembled.figures), "missing_figures": assembled.missing_figures,
                                   "warnings": assembled.warnings[:20]}
            for fmt in SCREEN_FORMATS:
                if fmt not in wanted and not (fmt == "pdf" and "latex" in wanted):
                    continue
                run = render(work, fmt, self.logs, timeout_s=self.settings.tools.render_timeout_s)
                entry: dict[str, Any] = {"ok": run.ok, "seconds": round(run.seconds, 1), "log": str(run.log_path)}
                if not run.ok:
                    entry["error"] = run.log_tail
                    results[fmt] = entry
                    continue
                out = work / "_output"
                if fmt == "html":
                    dest = exports / "html"
                    copy_tree(out, dest)
                    for stray in list(dest.glob("*.epub")) + list(dest.glob("*.pdf")):
                        stray.unlink()
                    entry["path"] = str(dest / "index.html")
                    preflight["html"] = check_html_site(dest)
                elif fmt == "epub":
                    epubs = sorted(out.glob("*.epub"))
                    if not epubs:
                        entry.update(ok=False, error="Quarto reported success but produced no EPUB")
                    else:
                        dest = exports / "epub" / "book.epub"
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(epubs[0], dest)
                        from scholarforge.publishing.epubfix import fix_epub

                        entry["postprocess"] = fix_epub(dest)
                        entry["path"] = str(dest)
                        java = find_tool("java")
                        jar = epubcheck_jar()
                        preflight["epub"] = check_epub(dest, {"java": java, "jar": jar} if java and jar else None)
                elif fmt == "pdf":
                    pdfs = sorted(out.glob("*.pdf"))
                    if not pdfs:
                        entry.update(ok=False, error="Quarto reported success but produced no PDF")
                    else:
                        dest = exports / "pdf" / "book.pdf"
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(pdfs[0], dest)
                        entry["path"] = str(dest)
                        entry["pages"] = _pages(dest)
                        preflight["pdf"] = preflight_pdf(dest, min_ppi=150)
                results[fmt] = entry
            if "latex" in wanted:
                results["latex"] = self._export_latex(work, exports / "latex")
            if "quarto" in wanted:
                dest = exports / "quarto-source"
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(work, dest, ignore=shutil.ignore_patterns("_output", ".quarto", "*.log", "*.aux",
                                                                          "*.idx", "*.ilg", "*.ind", "*.toc"))
                results["quarto"] = {"ok": True, "path": str(dest)}
        if "print" in wanted:
            results["print"], preflight["print"] = self._build_print(base / "print", exports / "print")
        ok_formats = [f for f in wanted if results.get(f, {}).get("ok")]
        status = "success" if len(ok_formats) == len(wanted) else ("partial" if ok_formats else "failed")
        from scholarforge.science.report import write_verification_report

        report = write_verification_report(self.project)
        manifest = {"build_id": build_id, "status": status, "built_at": now_iso(), "formats": wanted,
                    "results": results, "preflight": preflight, "external_edits": [e.node_id for e in edits],
                    "verification_report": report.get("markdown"), "seconds": round(time.monotonic() - started, 1)}
        atomic_write_json(exports / "manifest.json", manifest)
        atomic_write_text(self.project.reports_dir / "build-report.md", _build_report_md(manifest))
        self.pdb.builds.finish(build_id, status, {k: v.get("path") for k, v in results.items() if isinstance(v, dict)},
                               {"preflight_ok": {k: v.get("ok") for k, v in preflight.items()}})
        self.pdb.events.log("build.finished", "build", build_id, {"status": status, "formats": wanted})
        return manifest

    # -------------------------------------------------------------------------------- latex
    def _export_latex(self, work: Path, dest: Path) -> dict[str, Any]:
        texs = [p for p in work.glob("*.tex") if p.name not in ("preamble.tex", "after-body.tex")]
        if not texs:
            return {"ok": False, "error": "no .tex file kept (the PDF render must succeed first)"}
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        main = max(texs, key=lambda p: p.stat().st_size)
        shutil.copy2(main, dest / "book.tex")
        for name in ("preamble.tex", "after-body.tex", "references.bib", "references.json"):
            if (work / name).exists():
                shutil.copy2(work / name, dest / name)
        if (work / "figures").exists():
            shutil.copytree(work / "figures", dest / "figures")
        atomic_write_text(dest / "README.txt",
                          "LaTeX source exported by ScholarForge.\nCompile with: lualatex book.tex (run twice; "
                          "run makeindex book for the index). Bibliography is pre-rendered by citeproc; "
                          "references.bib is provided for reuse.\n")
        return {"ok": True, "path": str(dest / "book.tex")}

    # -------------------------------------------------------------------------------- print
    def _build_print(self, work: Path, dest: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        ps = self.project.config.print
        trim = ps.trim_mm()
        est = latex.estimate_pages(self.project)
        entry: dict[str, Any] = {"ok": False}
        assembler = BookAssembler(self.project, edition="print")
        cfg_geometry = latex.print_geometry(self.project.config, est)
        assembler.assemble(work)
        _patch_geometry(work, cfg_geometry)
        run = render(work, "pdf", self.logs, timeout_s=self.settings.tools.render_timeout_s,
                     log_name="render-print.log")
        if not run.ok:
            entry.update(error=run.log_tail, log=str(run.log_path))
            return entry, {}
        raw = sorted((work / "_output").glob("*.pdf"))[0]
        pages = _pages(raw)
        if ps.auto_gutter and latex.min_gutter_mm(pages) != latex.min_gutter_mm(est):
            _patch_geometry(work, latex.print_geometry(self.project.config, pages))
            run = render(work, "pdf", self.logs, timeout_s=self.settings.tools.render_timeout_s,
                         log_name="render-print-pass2.log")
            if not run.ok:
                entry.update(error=run.log_tail, log=str(run.log_path))
                return entry, {}
            raw = sorted((work / "_output").glob("*.pdf"))[0]
            pages = _pages(raw)
        dest.mkdir(parents=True, exist_ok=True)
        intent = resolve_output_intent(self.project) if ps.pdfx else None
        title = self.pdb.meta.get("book_title") or self.project.config.title
        author = ", ".join(self.project.config.params.authors) or "ScholarForge project"
        interior = dest / "interior.pdf"
        fin = finalize_print_pdf(raw, interior, trim_mm=trim, bleed_mm=ps.bleed_mm, color_mode=ps.color,
                                 title=title, author=author, intent=intent, label_pdfx=ps.pdfx)
        pre = {"interior": preflight_pdf(interior, trim_mm=trim, bleed_mm=ps.bleed_mm, expect_print=True,
                                         expect_pdfx=fin.pdfx_labelled, color_mode=ps.color, min_ppi=ps.min_image_ppi)}
        if fin.pdfx_labelled and not pre["interior"]["ok"]:
            # never ship a file that claims PDF/X-4 but fails our own checks: relabel as plain print PDF
            fin = finalize_print_pdf(raw, interior, trim_mm=trim, bleed_mm=ps.bleed_mm, color_mode=ps.color,
                                     title=title, author=author, intent=intent, label_pdfx=False)
            pre["interior"] = preflight_pdf(interior, trim_mm=trim, bleed_mm=ps.bleed_mm, expect_print=True,
                                            expect_pdfx=False, color_mode=ps.color, min_ppi=ps.min_image_ppi)
            pre["interior"]["note"] = "PDF/X-4 label withdrawn because the file did not pass all PDF/X checks"
        geo = cover_geometry(ps, pages)
        cover_entry: dict[str, Any] = {"geometry": geo.as_dict()}
        try:
            raw_cover = work / "cover-raw.pdf"
            build_cover(raw_cover, geo, title=title, subtitle=self.pdb.meta.get("book_subtitle"), author=author,
                        back_text=ps.cover.back_text, cover=ps.cover, gray=False)
            cover_pdf = dest / "cover.pdf"
            cover_trim = (geo.width - 2 * geo.bleed, geo.height - 2 * geo.bleed)
            cfin = finalize_print_pdf(raw_cover, cover_pdf, trim_mm=cover_trim, bleed_mm=geo.bleed, color_mode="color",
                                      title=f"{title} — cover", author=author, intent=intent, label_pdfx=ps.pdfx)
            pre["cover"] = preflight_pdf(cover_pdf, trim_mm=cover_trim, bleed_mm=geo.bleed, expect_print=True,
                                         expect_pdfx=cfin.pdfx_labelled, color_mode="color", min_ppi=ps.min_image_ppi)
            cover_entry.update(ok=True, path=str(cover_pdf), pdfx=cfin.pdfx_labelled)
        except Exception as exc:
            cover_entry.update(ok=False, error=f"{type(exc).__name__}: {exc}")
        specs = {"trim_mm": [round(trim[0], 3), round(trim[1], 3)], "bleed_mm": ps.bleed_mm, "pages": pages,
                 "binding": ps.binding, "paper": ps.paper, "per_page_thickness_mm": round(ps.per_page_thickness_mm(), 5),
                 "spine_mm": round(geo.spine, 3), "color": ps.color, "output_intent": fin.output_intent,
                 "pdfx_labelled": fin.pdfx_labelled, "color_conversion": fin.color.__dict__ if fin.color else None,
                 "notes": fin.notes, "cover": cover_entry, "geometry": latex.print_geometry(self.project.config, pages)}
        atomic_write_json(dest / "print-specs.json", specs)
        atomic_write_text(dest / "print-specs.md", _print_specs_md(specs, pre))
        entry.update(ok=pre["interior"]["ok"], path=str(interior), pages=pages, pdfx=fin.pdfx_labelled, specs=specs)
        return entry, pre

    def export_to(self, dest: Path) -> dict[str, Any]:
        src = self.project.exports_dir
        if not (src / "manifest.json").exists():
            raise WorkflowError("nothing to export yet; build first")
        dest = Path(dest).expanduser().resolve()
        if dest.exists() and any(dest.iterdir()):
            raise WorkflowError(f"destination is not empty: {dest}")
        shutil.copytree(src, dest, dirs_exist_ok=True)
        return {"exported_to": str(dest)}


def _patch_geometry(work: Path, geometry: list[str]) -> None:
    import yaml

    cfg_path = work / "_quarto.yml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg["format"]["pdf"]["geometry"] = geometry
    cfg["format"]["pdf"].pop("papersize", None)
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")


def _pages(pdf_path: Path) -> int:
    with pikepdf.open(pdf_path) as pdf:
        return len(pdf.pages)


def _print_specs_md(specs: dict[str, Any], pre: dict[str, Any]) -> str:
    lines = ["# Print specifications", "",
             f"- Trim size: {specs['trim_mm'][0]} × {specs['trim_mm'][1]} mm, bleed {specs['bleed_mm']} mm",
             f"- Pages: {specs['pages']}; binding: {specs['binding']}; paper: {specs['paper']} "
             f"({specs['per_page_thickness_mm']} mm per page)",
             f"- Spine width: {specs['spine_mm']} mm",
             f"- Colour: {specs['color']}; output intent: {specs['output_intent'] or 'none'}",
             f"- PDF/X-4 label: {'yes' if specs['pdfx_labelled'] else 'no'}", ""]
    for name, rep in pre.items():
        lines.append(f"## Preflight: {name} — {'PASS' if rep.get('ok') else 'FAIL'}")
        if rep.get("pdfx"):
            lines.append(f"*{rep['pdfx']}*")
        lines += [f"- ERROR: {e}" for e in rep.get("errors", [])] + [f"- warning: {w}" for w in rep.get("warnings", [])]
        if rep.get("not_checked"):
            lines.append("- not checked: " + "; ".join(rep["not_checked"]))
        lines.append("")
    if specs.get("notes"):
        lines += ["## Notes", ""] + [f"- {n}" for n in specs["notes"]]
    lines += ["", "Confirm trim size, bleed, spine width and colour requirements with your printer before upload."]
    return "\n".join(lines) + "\n"


def _build_report_md(m: dict[str, Any]) -> str:
    lines = [f"# Build report ({m['status']})", "", f"Built at {m['built_at']} in {m['seconds']} s.", ""]
    for fmt, res in m["results"].items():
        if not isinstance(res, dict):
            continue
        state = "ok" if res.get("ok", True) else "FAILED"
        lines.append(f"- **{fmt}**: {state} {res.get('path', '')}")
        if res.get("error"):
            lines += ["  ```", "  " + res["error"].replace("\n", "\n  ")[:3000], "  ```"]
    lines.append("")
    for name, rep in m["preflight"].items():
        if isinstance(rep, dict) and "ok" in rep:
            lines.append(f"- preflight {name}: {'pass' if rep['ok'] else 'FAIL'} {rep.get('errors', [])[:3]}")
        elif isinstance(rep, dict):
            for sub, r in rep.items():
                lines.append(f"- preflight {name}/{sub}: {'pass' if r.get('ok') else 'FAIL'} {r.get('errors', [])[:3]}")
    return "\n".join(lines) + "\n"
