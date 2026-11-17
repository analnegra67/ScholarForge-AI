import io
import zipfile

import pikepdf
import pytest

from scholarforge.illustrations.policy import check_python
from scholarforge.illustrations.sandbox import run_python_figure
from scholarforge.models.project import PrintSettings
from scholarforge.publishing.cover import cover_geometry
from scholarforge.publishing.pdfcolor import convert_pdf_colors
from scholarforge.publishing.pdfx import OutputIntent, finalize_print_pdf
from scholarforge.publishing.preflight import check_epub, check_html_site, preflight_pdf

PLOT = """
import numpy as np
import matplotlib.pyplot as plt
x = np.linspace(0, 1, 50)
fig, ax = plt.subplots()
ax.plot(x, x**2, color="tab:red")
ax.imshow(np.random.default_rng(0).random((8, 8)), extent=(0, 1, 0, 1), alpha=0.5)
"""


@pytest.mark.parametrize("code,needle", [
    ("open('C:/Windows/win.ini')", "name 'open'"),
    ("import os\nos.system('dir')", "import of 'os'"),
    ("x = ().__class__.__bases__", "private attribute"),
    ("import matplotlib.pyplot as plt\nplt.savefig('x.png')", "savefig"),
    ("import sympy", "import of 'sympy'"),
    ("import numpy as np\nnp.loadtxt('x')", "loadtxt"),
])
def test_policy_rejects_dangerous_code(code, needle):
    msgs = " ".join(v.message for v in check_python(code))
    assert needle in msgs


@pytest.fixture(scope="module")
def plot_pdf(tmp_path_factory):
    res = run_python_figure(PLOT, timeout_s=120)
    assert res.ok, res.error
    return res


def test_sandbox_renders_vector_and_raster(plot_pdf):
    assert set(plot_pdf.files) == {"pdf", "svg", "png"}
    with pikepdf.open(plot_pdf.files["pdf"]) as pdf:
        assert len(pdf.pages) == 1


def test_sandbox_runtime_errors_are_reported():
    res = run_python_figure("import numpy as np\nimport matplotlib.pyplot as plt\nplt.plot([1, 2], [1, 2, 3])",
                            timeout_s=120)
    assert not res.ok and "ValueError" in res.error and "line 3" in res.error


def test_sandbox_timeout():
    res = run_python_figure("while True:\n    pass", timeout_s=6)
    assert not res.ok and "timed out" in res.error


def test_color_conversion_icc_and_gray(plot_pdf, tmp_path):
    icc = tmp_path / "icc.pdf"
    rep = convert_pdf_colors(plot_pdf.files["pdf"], icc, "icc")
    assert rep.rgb_ops_converted > 0 and rep.images_converted >= 1 and not rep.unsupported
    pre = preflight_pdf(icc, min_ppi=10)
    assert not any("DeviceRGB" in k for k in pre["info"]["colorspaces"]), pre["info"]["colorspaces"]
    gray = tmp_path / "gray.pdf"
    rep = convert_pdf_colors(plot_pdf.files["pdf"], gray, "gray")
    cs = preflight_pdf(gray, min_ppi=10)["info"]["colorspaces"]
    assert not any(k in ("DeviceRGB", "image:DeviceRGB") for k in cs) and rep.images_converted >= 1


def _cmyk_profile():
    """A real CMYK printer profile available on this machine (never shipped with ScholarForge)."""
    import os
    from pathlib import Path

    from scholarforge.tools.detect import tex_find_file

    found = tex_find_file("FOGRA39L_coated.icc")
    if found:
        return Path(found)
    win = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32" / "spool" / "drivers" / "color" / "RSWOP.icm"
    return win if win.exists() else None


def test_icc_header_parsing(tmp_path):
    from PIL import ImageCms

    from scholarforge.publishing.pdfx import icc_info

    srgb = icc_info(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    assert srgb["valid"] and srgb["device_class"] == "mntr" and srgb["components"] == 3
    assert not icc_info(b"not an icc profile")["valid"]


def test_display_profile_is_refused_as_output_intent(plot_pdf, tmp_path):
    from PIL import ImageCms

    icc_path = tmp_path / "srgb.icc"
    icc_path.write_bytes(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    rep = finalize_print_pdf(plot_pdf.files["pdf"], tmp_path / "o.pdf", trim_mm=(100, 100), bleed_mm=0,
                             color_mode="color", title="T", author="A", intent=OutputIntent(icc_path=icc_path))
    assert not rep.pdfx_labelled and rep.output_intent is None
    assert any("not a printer" in n for n in rep.notes)


def test_finalize_and_preflight_print_pdf(plot_pdf, tmp_path):
    icc_path = _cmyk_profile()
    if icc_path is None:
        pytest.skip("no CMYK printer ICC profile available on this machine")
    src = tmp_path / "in.pdf"
    # make a page sized trim+bleed: 100x150 mm trim, 3 mm bleed
    with pikepdf.open(plot_pdf.files["pdf"]) as pdf:
        mm = 72 / 25.4
        pdf.pages[0].obj.MediaBox = pikepdf.Array([0, 0, 106 * mm, 156 * mm])
        pdf.save(src)
    out = tmp_path / "print.pdf"
    rep = finalize_print_pdf(src, out, trim_mm=(100, 150), bleed_mm=3, color_mode="color", title="T", author="A",
                             intent=OutputIntent(icc_path=icc_path, identifier="TEST", info="test profile"))
    assert rep.boxes_set and rep.output_intent == "TEST"
    pre = preflight_pdf(out, trim_mm=(100, 150), bleed_mm=3, expect_print=True, expect_pdfx=rep.pdfx_labelled,
                        min_ppi=1)
    assert pre["ok"], pre["errors"]
    assert pre["info"]["xmp_pdfx_version"] == "PDF/X-4"
    # wrong trim is detected
    bad = preflight_pdf(out, trim_mm=(110, 150), bleed_mm=3, expect_print=True, min_ppi=1)
    assert not bad["ok"]


def test_preflight_detects_rgb_and_missing_boxes(plot_pdf):
    rep = preflight_pdf(plot_pdf.files["pdf"], trim_mm=(100, 100), bleed_mm=3, expect_print=True, min_ppi=1)
    joined = " ".join(rep["errors"])
    assert "TrimBox" in joined and "device RGB" in joined


def test_cover_geometry_kdp_numbers():
    ps = PrintSettings(trim="6x9in", paper="kdp-white", bleed_mm=3.175)
    geo = cover_geometry(ps, 300)
    assert abs(geo.spine - 300 * 0.002252 * 25.4) < 1e-9  # 17.16 mm
    assert abs(geo.width - (2 * 152.4 + geo.spine + 2 * 3.175)) < 1e-9
    assert cover_geometry(ps, 301).pages == 302  # odd page counts are rounded up to whole leaves
    ppi = PrintSettings(trim="6x9in", paper="custom", ppi=444)
    assert abs(cover_geometry(ppi, 444).spine - 25.4) < 1e-6


def _epub(entries: dict[str, bytes], mimetype_first=True, stored=True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if mimetype_first:
            zf.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip",
                        compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
        for name, data in entries.items():
            zf.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def test_epub_structure_checks(tmp_path):
    container = (b'<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 b'<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 b'</rootfiles></container>')
    opf = (b'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
           b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>T</dc:title><dc:language>en</dc:language>'
           b'</metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
           b'<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest></package>')
    xhtml = b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>x</p></body></html>'
    good = tmp_path / "good.epub"
    good.write_bytes(_epub({"META-INF/container.xml": container, "OEBPS/content.opf": opf, "OEBPS/nav.xhtml": xhtml,
                            "OEBPS/c1.xhtml": xhtml}))
    assert check_epub(good)["ok"]
    bad = tmp_path / "bad.epub"
    bad.write_bytes(_epub({"META-INF/container.xml": container, "OEBPS/content.opf": opf, "OEBPS/nav.xhtml": xhtml,
                           "OEBPS/c1.xhtml": b"<html><body><p>unclosed</body></html>"}, stored=False))
    errs = " ".join(check_epub(bad)["errors"])
    assert "uncompressed" in errs and "well-formed" in errs


def test_html_link_check(tmp_path):
    (tmp_path / "index.html").write_text('<a href="a.html#x">ok</a><a href="missing.html">bad</a>', encoding="utf-8")
    (tmp_path / "a.html").write_text('<h1 id="x">A</h1><img src="img.png">', encoding="utf-8")
    rep = check_html_site(tmp_path)
    assert not rep["ok"] and rep["info"]["broken_links"] == 2
