"""Build PDF copies of the OpenBedside documents into docs/pdf/.

The Markdown files are the source. The PDFs are generated from them, so edit the
Markdown and run this again. Not part of the gateway and not needed to run it.

Needs:  pip install markdown playwright   and a Chromium that Playwright can find.
Run:    python tools/build_pdfs.py            (from the OpenBedside folder)
        CHROMIUM=/path/to/chrome python tools/build_pdfs.py
"""
from __future__ import annotations

import html
import os
import re
import sys
from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "pdf"
from importlib.metadata import version as _v
try:
    VERSION = _v("openbedside")
except Exception:
    VERSION = "0.2.0"

# (source, pdf name, title) in handbook order
DOCS = [
    ("README.md", "OpenBedside-README.pdf", "Read me"),
    ("START-HERE-install-on-your-Mac.md", "OpenBedside-Start-Here-Install.pdf", "Start here: install and run"),
    ("docs/USER-GUIDE.md", "OpenBedside-User-Guide.pdf", "User guide"),
    ("docs/VENDOR-INTEGRATION-GUIDE.md", "OpenBedside-Vendor-Integration-Guide.pdf", "Vendor integration guide"),
    ("docs/MAPPING-WORKSHEET.md", "OpenBedside-Mapping-Worksheet.pdf", "Mapping worksheet"),
    ("docs/ADAPTER-SDK.md", "OpenBedside-Adapter-Reference.pdf", "Adapter reference"),
    ("docs/HL7-INTERFACE.md", "OpenBedside-HL7-Interface.pdf", "HL7 interface"),
    ("docs/ARCHITECTURE.md", "OpenBedside-Architecture.pdf", "Architecture"),
    ("docs/DECISIONS.md", "OpenBedside-Decisions.pdf", "Decisions"),
    ("CONTRIBUTING.md", "OpenBedside-Contributing.pdf", "Contributing"),
    ("DISCLAIMER.md", "OpenBedside-Disclaimer.pdf", "Disclaimer"),
]
PDF_OF = {Path(s).name: p for s, p, _ in DOCS}
ANCHOR_OF = {Path(s).name: "doc-" + re.sub(r"[^a-z0-9]+", "-", Path(s).stem.lower()) for s, _, _ in DOCS}

CSS = """
@page { size: Letter; margin: 0.8in 0.85in 0.85in 0.85in; }
:root { --ink:#1f2d3a; --navy:#2C3E50; --blue:#5D7A94; --grey:#8FA3B3; --pale:#EEF2F5; --line:#C9D3DC; }
body { font-family: "DejaVu Sans", "Liberation Sans", Arial, sans-serif; font-size: 10pt;
       line-height: 1.5; color: var(--ink); background: #fff; }
h1 { font-size: 20pt; color: var(--navy); border-bottom: 3px solid var(--blue);
     padding-bottom: 6px; margin: 0 0 14px; }
h2 { font-size: 13.5pt; color: var(--navy); margin: 22px 0 8px; border-bottom: 1px solid var(--line);
     padding-bottom: 3px; page-break-after: avoid; }
h3 { font-size: 11pt; color: var(--blue); margin: 16px 0 6px; page-break-after: avoid; }
p, li { orphans: 3; widows: 3; }
a { color: var(--blue); text-decoration: none; border-bottom: 1px dotted var(--grey); }
strong { color: var(--navy); }
code { font-family: "DejaVu Sans Mono", "Liberation Mono", monospace; font-size: 8.8pt;
       background: var(--pale); padding: 1px 4px; border-radius: 3px; }
pre { background: var(--pale); border-left: 4px solid var(--blue); padding: 9px 12px;
      font-size: 8.3pt; line-height: 1.35; white-space: pre; overflow: hidden;
      page-break-inside: avoid; border-radius: 0 4px 4px 0; }
pre code { background: none; padding: 0; font-size: inherit; }
table { border-collapse: collapse; width: 100%; margin: 10px 0 14px; font-size: 9pt;
        page-break-inside: auto; }
tr { page-break-inside: avoid; }
th { background: var(--navy); color: #fff; text-align: left; padding: 6px 8px; font-weight: bold; }
td { border-bottom: 1px solid var(--line); padding: 5px 8px; vertical-align: top; }
tr:nth-child(even) td { background: #F6F8FA; }
blockquote { margin: 12px 0; padding: 8px 14px; background: var(--pale);
             border-left: 4px solid var(--navy); color: var(--navy); }
blockquote p { margin: 4px 0; }
img { max-width: 100%; border: 1px solid var(--line); border-radius: 4px; }
hr { border: 0; border-top: 1px solid var(--line); margin: 18px 0; }
.worksheet td:last-child { width: 44%; height: 26px; }
.worksheet tr:nth-child(even) td { background: #fff; }
.blank { display: inline-block; min-width: 1.7in; border-bottom: 1px solid var(--ink); }
.doc { page-break-before: always; }
.cover { height: 9.1in; display: flex; flex-direction: column; justify-content: center; }
.cover .band { background: var(--navy); color: #fff; padding: 34px 36px; border-radius: 6px; }
.cover .band h1 { color: #fff; border: 0; font-size: 30pt; margin: 0 0 6px; }
.cover .band p { margin: 0; color: #D6DEE6; font-size: 12pt; }
.cover .meta { margin-top: 26px; color: var(--blue); font-size: 10.5pt; }
.cover .warn { margin-top: 40px; border: 1px solid var(--grey); padding: 10px 14px; color: var(--navy);
               font-size: 9.5pt; border-radius: 4px; }
.toc { page-break-before: always; }
.toc ol { font-size: 11pt; line-height: 2; }
"""

FOOTER = ('<div style="width:100%;font-size:7.5pt;color:#8FA3B3;font-family:DejaVu Sans,sans-serif;'
          'padding:0 0.85in;display:flex;justify-content:space-between;">'
          '<span>OpenBedside {v} &middot; open source &middot; Apache 2.0 &middot; not a medical device</span>'
          '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')


def render(src: Path, handbook: bool) -> str:
    text = src.read_text(encoding="utf-8")
    # fill-in blanks (runs of underscores) would otherwise be read as emphasis
    text = re.sub(r"_{3,}", '<span class="blank"></span>', text)
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])

    def fix_link(m: re.Match) -> str:
        href = m.group(1)
        if href.startswith(("http://", "https://", "mailto:", "#")):
            return m.group(0)
        name = Path(href.split("#")[0]).name
        if handbook and name in ANCHOR_OF:
            return f'href="#{ANCHOR_OF[name]}"'
        if name in PDF_OF:
            return f'href="{PDF_OF[name]}"'
        return m.group(0)

    body = re.sub(r'href="([^"]+)"', fix_link, body)
    # images: make paths absolute so Chromium finds them
    body = re.sub(r'src="([^":]+)"', lambda m: f'src="{(src.parent / m.group(1)).resolve().as_uri()}"', body)
    if src.name == "MAPPING-WORKSHEET.md":
        body = f'<div class="worksheet">{body}</div>'
    return body


def page(title: str, inner: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style></head><body>{inner}</body></html>")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    exe = os.environ.get("CHROMIUM")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 653, "height": 1000})  # printable width of Letter at these margins
        pg.emulate_media(media="print")

        def to_pdf(doc_html: str, dest: Path) -> None:
            tmp = OUT / "_tmp.html"
            tmp.write_text(doc_html, encoding="utf-8")
            pg.goto(tmp.as_uri())
            pg.wait_for_load_state("networkidle")
            # shrink any code block that is wider than the page (the architecture diagram)
            pg.evaluate("""() => { for (const b of document.querySelectorAll('pre')) {
                let f = 8.3; while (b.scrollWidth > b.clientWidth + 1 && f > 5) { f -= 0.2; b.style.fontSize = f + 'pt'; } } }""")
            pg.pdf(path=str(dest), format="Letter", print_background=True, display_header_footer=True,
                   header_template="<div></div>", footer_template=FOOTER.format(v=VERSION),
                   margin={"top": "0.75in", "bottom": "0.8in", "left": "0.85in", "right": "0.85in"})
            tmp.unlink()
            print("wrote", dest.relative_to(ROOT))

        for src, pdf, title in DOCS:
            to_pdf(page(f"OpenBedside: {title}", render(ROOT / src, False)), OUT / pdf)

        cover = (
            "<div class='cover'><div class='band'><h1>OpenBedside Handbook</h1>"
            "<p>An open-source gateway between bedside medical devices and the hospital EHR.</p>"
            "<p>IV infusion pumps first. Built to take ventilators and other devices.</p></div>"
            f"<div class='meta'>Version {VERSION} &middot; Daniel C. Pettus &middot; Apache License 2.0<br>"
            "Every guide in the kit, in one file. The Markdown files in the kit are the source.</div>"
            "<div class='warn'><strong>Not a medical device. Not for clinical use.</strong> "
            "Use on test networks only. See the Disclaimer at the end.</div></div>")
        toc = "<div class='toc'><h1>Contents</h1><ol>" + "".join(
            f"<li><a href='#{ANCHOR_OF[Path(s).name]}'>{html.escape(t)}</a></li>" for s, _, t in DOCS) + "</ol></div>"
        parts = "".join(f"<section class='doc' id='{ANCHOR_OF[Path(s).name]}'>{render(ROOT / s, True)}</section>"
                        for s, _, _ in DOCS)
        to_pdf(page("OpenBedside Handbook", cover + toc + parts), OUT / f"OpenBedside-Handbook-v{VERSION}.pdf")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
