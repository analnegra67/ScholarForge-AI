---
name: scholarforge-publish
description: Build, export and preflight a ScholarForge book — HTML, EPUB 3, PDF, print-ready PDF with cover (PDF/X-4 when achievable), LaTeX and Quarto sources; configure trim size, bleed, paper, colour and printer requirements.
---

# Publishing a ScholarForge book

- `configure_project(project_id, print_settings={...})` sets the print edition. Common keys: `trim` (`6x9in`,
  `7x10in`, `a4`, `b5`, or `170x240mm`), `bleed_mm` (3.175 = 0.125 in), `binding` (perfect, case, saddle, coil),
  `paper` (`kdp-white`, `kdp-cream`, `offset-80gsm`, … or `custom` with `ppi` / `page_thickness_mm` from the
  printer), `color` (`bw` or `color`), `pdfx` (true), `icc_profile` (`auto` = FOGRA39 from TeX, or a file the
  printer supplied), `cover` (background, text_color, back_text…).
- `build_book(project_id, formats=[...])` rebuilds everything from the stored sources; it never asks you to rewrite
  text. Formats: html, epub, pdf, print, latex, quarto. It can take several minutes (LaTeX packages are installed
  automatically the first time).
- `preflight_report(project_id)` gives the independent checks: page geometry and boxes, fonts embedded, colour
  spaces, image resolution, annotations, PDF/X-4 identification, EPUB structure and epubcheck, HTML links.
- Tell the user honestly what the preflight does not cover (ink limits, trapping, the printer's own profile) and
  that the printer's preflight is authoritative. A PDF is labelled PDF/X-4 only if it passes ScholarForge's checks.
- The spine width is computed from the real page count and the paper data: if the printer quotes pages-per-inch
  (PPI), set `paper: custom` and `ppi`.
