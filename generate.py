#!/usr/bin/env python3
"""
Usage:
    python generate.py example-note.md
    python generate.py my-note.md --output ActiveDirectory-Notes.pdf
    python generate.py my-note.md --html     # Preview in browser first
    python generate.py my-note.md --open     # Open PDF after generation

Markdown front matter (place at top of .md file):
    ---
    title: "Active Directory Attacks"
    subtitle: "Techniques, Tools & Defences"
    category: "Red Team"
    author: "RootSec"
    date: "2026-04-02"
    version: "1.0"
    logo: "assets/rootsec-logo.png"   # relative to .md file, optional
    two_column: false
    ---
"""

import argparse
import base64
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import frontmatter
import jinja2
import markdown as md_lib
from markdown.extensions.toc import TocExtension

BASE_DIR = Path(__file__).parent
TEMPLATE_DIR = BASE_DIR / "template"
ASSETS_DIR = TEMPLATE_DIR / "assets"

def img_to_b64(path) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    suffix = p.suffix.lower().lstrip(".")
    mime_map = {
        "jpg": "jpeg", "jpeg": "jpeg", "png": "png",
        "gif": "gif", "svg": "svg+xml", "webp": "webp",
    }
    mime = mime_map.get(suffix, "png")
    encoded = base64.b64encode(p.read_bytes()).decode()
    return f"data:image/{mime};base64,{encoded}"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def rewrite_img_paths(html: str, note_dir: Path) -> str:
    """Replace relative image src paths with base64 data URIs."""
    def replace(match):
        src = match.group(1)
        if src.startswith("data:") or src.startswith("http"):
            return match.group(0)
        data = img_to_b64(note_dir / src)
        return f'src="{data}"' if data else match.group(0)
    return re.sub(r'src="([^"]+)"', replace, html)

def _mermaid_fence(source, language, css_class, options, md, **kwargs) -> str:
    """Custom pymdownx fence: wraps mermaid blocks in a div for JS rendering."""
    return f'<div class="mermaid">{source}</div>'


def convert_markdown(text: str) -> tuple[str, str]:
    md = md_lib.Markdown(
        extensions=[
            "fenced_code",
            "tables",
            "nl2br",
            "attr_list",
            "sane_lists",
            "pymdownx.highlight",
            "pymdownx.superfences",
            "pymdownx.tasklist",
            "admonition",
            TocExtension(permalink=False, toc_depth="2-3"),
        ],
        extension_configs={
            "pymdownx.highlight": {
                "use_pygments": True,
                "linenums": False,
                "guess_lang": True,
            },
            "pymdownx.superfences": {
                "disable_indented_code_blocks": False,
                "custom_fences": [
                    {
                        "name": "mermaid",
                        "class": "mermaid",
                        "format": _mermaid_fence,
                    }
                ],
            },
        },
    )
    content_html = md.convert(text)
    toc_html = md.toc if hasattr(md, "toc") else ""
    return content_html, toc_html


##### JINJA RENDERING #####

def load_logo(meta: dict, note_dir: Path) -> str:
    """Return base64 data URI for the logo, falling back to logo.png in BASE_DIR."""
    logo_rel = meta.get("logo", "")
    if logo_rel:
        return img_to_b64(note_dir / logo_rel)
    default_logo = BASE_DIR / "logo.png"
    return img_to_b64(default_logo) if default_logo.exists() else ""


def render_html(meta: dict, content_html: str, toc_html: str, note_dir: Path,
                logo_data: str = "") -> str:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )

    css = (ASSETS_DIR / "style.css").read_text(encoding="utf-8")
    content_html = rewrite_img_paths(content_html, note_dir)

    template = env.get_template("note.html.j2")
    return template.render(
        meta=meta,
        content_html=content_html,
        toc_html=toc_html,
        logo_data=logo_data,
        css_content=css,
        generation_date=datetime.now().strftime("%B %d, %Y"),
    )

def build_header_html(logo_data: str = "") -> str:
    if not logo_data:
        return "<span></span>"
    return (
        '<div style="width:100%;box-sizing:border-box;padding:8px 36px 0 0;'
        'display:flex;justify-content:flex-end;align-items:center;'
        '-webkit-print-color-adjust:exact;print-color-adjust:exact;">'
        f'<img src="{logo_data}" style="height:26px;object-fit:contain;'
        'filter:invert(1);opacity:0.85;">'
        '</div>'
    )


def build_footer_html(author: str, title: str, category: str) -> str:
    return (
        '<div style="width:100%;font-family:Inter,\'Segoe UI\',sans-serif;'
        '-webkit-print-color-adjust:exact;print-color-adjust:exact;'
        'box-sizing:border-box;padding-bottom:16px;">'
        '<div style="padding:0 72px;">'
        '<div style="text-align:right;padding-bottom:5px;'
        'font-size:9px;color:#b0b8c8;font-weight:500;line-height:1;">'
        '<span class="pageNumber"></span>'
        '</div>'
        '<div style="border-top:1px solid #e63946;margin-bottom:5px;"></div>'
        '<div style="display:flex;justify-content:space-between;align-items:center;'
        'font-size:9px;color:#6b7280;">'
        f'<span style="font-weight:500;color:#b0b8c8">{author}</span>'
        f'<span>{title}</span>'
        f'<span>{category}</span>'
        '</div>'
        '</div>'
        '</div>'
    )


def html_to_pdf(html_path: str, output_path: str,
                header_html: str, footer_html: str) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Error: playwright not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        print("Error: pypdf not installed. Run: pip install pypdf")
        sys.exit(1)

    html_p = Path(html_path)
    tmp_body  = str(html_p.parent / "_tmp_note_body.pdf")
    tmp_cover = str(html_p.parent / "_tmp_note_cover.pdf")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        pw_page = browser.new_page()
        pw_page.goto(html_p.resolve().as_uri(), wait_until="networkidle")

        try:
            pw_page.wait_for_function(
                """() => {
                    const diagrams = document.querySelectorAll('.mermaid');
                    if (diagrams.length === 0) return true;
                    return Array.from(diagrams).every(el => el.querySelector('svg') !== null);
                }""",
                timeout=20000,
            )
        except Exception:
            pass

        pw_page.pdf(
            path=tmp_body,
            format="A4",
            print_background=True,
            display_header_footer=True,
            header_template=header_html,
            footer_template=footer_html,
            margin={"top": "88px", "bottom": "80px", "left": "0px", "right": "0px"},
        )

        pw_page.pdf(
            path=tmp_cover,
            format="A4",
            print_background=True,
            display_header_footer=False,
            margin={"top": "0px", "bottom": "0px", "left": "0px", "right": "0px"},
        )

        browser.close()

    try:
        body_count = len(PdfReader(tmp_body).pages)
        writer = PdfWriter()
        writer.append(tmp_cover, pages=(0, 1))         
        if body_count > 1:
            writer.append(tmp_body, pages=(1, body_count))
        with open(output_path, "wb") as f:
            writer.write(f)
    finally:
        Path(tmp_body).unlink(missing_ok=True)
        Path(tmp_cover).unlink(missing_ok=True)

def main():
    parser = argparse.ArgumentParser(
        description="RootSec Notes PDF Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("markdown_file", help="Path to .md note file")
    parser.add_argument("--output", "-o", default=None, help="Output PDF/HTML path")
    parser.add_argument("--html", action="store_true", help="Output HTML only")
    parser.add_argument("--open", action="store_true", help="Open output after generation")
    args = parser.parse_args()

    md_path = Path(args.markdown_file)
    if not md_path.exists():
        print(f"Error: File not found: {md_path}", file=sys.stderr)
        sys.exit(1)

    note_dir = md_path.parent

    print(f"[1/3] Parsing {md_path.name} ...")
    post = frontmatter.load(str(md_path))
    meta = dict(post.metadata)
    meta.setdefault("title", md_path.stem.replace("-", " ").replace("_", " ").title())
    meta.setdefault("subtitle", "")
    meta.setdefault("category", "Notes")
    meta.setdefault("author", "RootSec")
    meta.setdefault("date", datetime.now().strftime("%Y-%m-%d"))
    meta.setdefault("version", "1.0")
    meta.setdefault("two_column", False)

    content_html, toc_html = convert_markdown(post.content)
    logo_data = load_logo(meta, note_dir)

    print("[2/3] Rendering HTML template ...")
    html = render_html(meta, content_html, toc_html, note_dir, logo_data)

    file_slug = slug(meta["title"])

    if args.html:
        out = args.output or f"{file_slug}.html"
        Path(out).write_text(html, encoding="utf-8")
        print(f"      HTML written to: {out}")
        _maybe_open(out, args.open)
        return

    out = args.output or f"{file_slug}.pdf"

    print(f"[3/3] Generating PDF → {out} ...")
    header_html = build_header_html(logo_data)
    footer_html = build_footer_html(meta["author"], meta["title"], meta["category"])

    tmp_html = str(TEMPLATE_DIR.parent / "_tmp_note.html")
    Path(tmp_html).write_text(html, encoding="utf-8")
    try:
        html_to_pdf(tmp_html, out, header_html, footer_html)
    finally:
        Path(tmp_html).unlink(missing_ok=True)

    print(f"      Done — saved to: {out}")
    _maybe_open(out, args.open)


def _maybe_open(path: str, do_open: bool) -> None:
    if not do_open:
        return
    import platform, subprocess
    if platform.system() == "Windows":
        os.startfile(os.path.abspath(path))
    elif platform.system() == "Darwin":
        subprocess.run(["open", path])
    else:
        subprocess.run(["xdg-open", path])


if __name__ == "__main__":
    main()
