"""Artifact exporters: Markdown, DOCX, JSON and searchable PDF."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.core.artifacts.model import (
    ArtifactBlock,
    ArtifactDocument,
    ArtifactSection,
    parse_artifact,
    strip_citations,
)
from backend.platform.paths import DataPaths

logger = logging.getLogger(__name__)

_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]+')


class ArtifactExporter:
    def __init__(self, paths: DataPaths) -> None:
        self.paths = paths

    def output_dir(self, artifact_id: str, custom: Path | None = None) -> Path:
        directory = custom or (self.paths.artifacts_dir / artifact_id)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    async def export(
        self,
        *,
        artifact_id: str,
        title: str,
        markdown: str,
        structure: dict[str, Any],
        format: str,
        output_dir: Path | None = None,
        include_citations: bool = True,
    ) -> Path:
        directory = self.output_dir(artifact_id, output_dir)
        stem = _ILLEGAL.sub("_", title).strip("_")[:80] or artifact_id
        filename = f"{stem}_{artifact_id[:8]}.{format}"
        path = directory / filename

        if format == "md":
            path.write_text(markdown, encoding="utf-8")
            return path
        if format == "json":
            payload = {
                "artifact_id": artifact_id,
                "title": title,
                "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "structure": structure,
                "markdown": markdown,
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return path

        effective_markdown = markdown if include_citations else strip_citations(markdown)
        document = parse_artifact(effective_markdown, fallback_title=title)

        if format == "docx":
            await _write_docx(document, path)
            return path
        if format == "pdf":
            await _write_pdf(effective_markdown, path)
            return path
        raise ValueError(f"不支持的导出格式: {format}")


async def _write_docx(document: ArtifactDocument, path: Path) -> None:
    import asyncio

    await asyncio.to_thread(_write_docx_sync, document, path)


def _write_docx_sync(document: ArtifactDocument, path: Path) -> None:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm

    doc = Document()
    for section in doc.sections:
        section.top_margin = Cm(2.2)
        section.bottom_margin = Cm(2.0)
        section.left_margin = Cm(2.2)
        section.right_margin = Cm(2.0)

    _configure_styles(doc, qn)

    title_paragraph = doc.add_heading(document.title, level=0)
    title_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

    for section in document.sections:
        _add_section(doc, section, qn)
    doc.save(str(path))


def _configure_styles(doc, qn) -> None:
    from docx.shared import Pt

    styles = doc.styles
    for name, size in (("Normal", 10.5), ("Title", 22), ("Heading 1", 16), ("Heading 2", 14), ("Heading 3", 12)):
        try:
            style = styles[name]
        except KeyError:
            continue
        style.font.name = "Microsoft YaHei"
        style.font.size = Pt(size)
        element = style.element
        rpr = element.get_or_add_rPr()
        fonts = rpr.find(qn("w:rFonts"))
        if fonts is None:
            from docx.oxml import OxmlElement

            fonts = OxmlElement("w:rFonts")
            rpr.append(fonts)
        fonts.set(qn("w:eastAsia"), "Microsoft YaHei")


def _add_section(doc, section: ArtifactSection, qn) -> None:
    from docx.shared import Cm, Pt

    if section.title and section.title != "正文":
        doc.add_heading(section.title, level=min(9, max(1, section.level)))
    for block in section.blocks:
        if block.kind == "paragraph" and block.text:
            _add_paragraph(doc, block.text)
        elif block.kind == "list":
            style = "List Number" if block.meta.get("ordered") else "List Bullet"
            for item in block.items:
                _add_paragraph(doc, item, style=style)
        elif block.kind == "table" and block.rows:
            _add_table(doc, block, qn)
        elif block.kind == "code" and block.text:
            paragraph = _add_paragraph(doc, block.text)
            for run in paragraph.runs:
                run.font.name = "Consolas"
                run.font.size = Pt(9)
        elif block.kind == "quote" and block.text:
            paragraph = _add_paragraph(doc, block.text)
            paragraph.paragraph_format.left_indent = Cm(0.8)
            for run in paragraph.runs:
                run.italic = True
        elif block.kind == "image":
            source = str(block.meta.get("src") or "")
            candidate = Path(source)
            if candidate.is_file():
                try:
                    doc.add_picture(str(candidate), width=Cm(14))
                except Exception as exc:  # pragma: no cover - invalid image
                    logger.warning("DOCX 图片插入失败 %s: %s", candidate, exc)
    for child in section.children:
        _add_section(doc, child, qn)


def _add_paragraph(doc, text: str, *, style: str | None = None):
    from docx.oxml.ns import qn

    paragraph = doc.add_paragraph(style=style) if style else doc.add_paragraph()
    run = paragraph.add_run(text)
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    return paragraph


def _add_table(doc, block: ArtifactBlock, qn) -> None:
    from docx.shared import Pt

    rows = block.rows
    columns = max(len(row) for row in rows)
    table = doc.add_table(rows=len(rows), cols=columns)
    table.style = "Table Grid"
    for row_index, row in enumerate(rows):
        for column_index in range(columns):
            cell = table.cell(row_index, column_index)
            value = row[column_index] if column_index < len(row) else ""
            cell.text = ""
            paragraph = cell.paragraphs[0]
            run = paragraph.add_run(value)
            run.font.name = "Microsoft YaHei"
            run.font.size = Pt(9.5)
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
            if row_index == 0:
                run.bold = True


async def _write_pdf(markdown: str, path: Path) -> None:
    from markdown_it import MarkdownIt

    body = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"]).render(
        markdown
    )
    html_text = _HTML_TEMPLATE.format(body=body)

    try:
        await _render_pdf_playwright(html_text, path)
        return
    except Exception as exc:  # pragma: no cover - host dependent
        logger.warning("Playwright PDF 渲染不可用，尝试系统 Edge/Chrome: %s", exc)

    import asyncio

    if not await asyncio.to_thread(_render_pdf_browser_cli, html_text, path):
        raise RuntimeError(
            "PDF 渲染失败：未找到可用的 Chromium/Edge/Chrome。"
            "请安装浏览器，或在开发环境中执行 playwright install chromium。"
        )


async def _render_pdf_playwright(html_text: str, path: Path) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = None
        errors: list[str] = []
        for options in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
            try:
                browser = await playwright.chromium.launch(**options)
                break
            except Exception as exc:  # pragma: no cover - host dependent
                errors.append(f"{options or 'bundled'}: {exc}"[:160])
        if browser is None:
            raise RuntimeError("无法启动 Chromium/Edge: " + "; ".join(errors))
        try:
            page = await browser.new_page()
            await page.set_content(html_text, wait_until="load")
            await page.pdf(
                path=str(path),
                format="A4",
                print_background=True,
                margin={"top": "20mm", "bottom": "18mm", "left": "18mm", "right": "18mm"},
                display_header_footer=True,
                header_template="<span></span>",
                footer_template=(
                    '<div style="width:100%;font-size:9px;text-align:center;color:#666;">'
                    '<span class="pageNumber"></span> / <span class="totalPages"></span></div>'
                ),
            )
        finally:
            await browser.close()


def _browser_candidates() -> list[str]:
    import os
    import sys

    candidates: list[str] = []
    env = os.environ.get("EDUCORE_BROWSER_PATH")
    if env:
        candidates.append(env)
    if sys.platform == "win32":
        candidates.extend(
            [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ]
        )
    elif sys.platform == "darwin":
        candidates.extend(
            [
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
            ]
        )
    else:
        candidates.extend(["/usr/bin/chromium", "/usr/bin/google-chrome", "/usr/bin/microsoft-edge"])
    return candidates


def _render_pdf_browser_cli(html_text: str, path: Path) -> bool:
    """Fallback renderer for packaged builds without Playwright/Chromium."""
    import subprocess
    import tempfile

    executable = next((item for item in _browser_candidates() if Path(item).is_file()), None)
    if executable is None:
        return False

    with tempfile.TemporaryDirectory() as directory:
        html_path = Path(directory) / "artifact.html"
        html_path.write_text(html_text, encoding="utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            executable,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={path}",
            html_path.as_uri(),
        ]
        try:
            subprocess.run(
                command,
                check=False,
                timeout=120,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("系统浏览器 PDF 渲染失败: %s", exc)
            return False
    return path.is_file() and path.stat().st_size > 1000


_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<style>
  @page {{ size: A4; margin: 18mm 18mm 16mm; }}
  body {{
    font-family: "Microsoft YaHei", "Noto Sans CJK SC", "PingFang SC", sans-serif;
    font-size: 10.5pt;
    line-height: 1.75;
    color: #1c1f1d;
    margin: 0;
  }}
  h1, h2, h3, h4 {{ line-height: 1.35; margin: 1.1em 0 0.5em; page-break-after: avoid; }}
  h1 {{ font-size: 20pt; }}
  h2 {{ font-size: 15pt; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
  h3 {{ font-size: 12.5pt; }}
  p {{ margin: 0.4em 0; }}
  ul, ol {{ padding-left: 1.6em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 0.8em 0; page-break-inside: avoid; }}
  th, td {{ border: 1px solid #bbb; padding: 5px 7px; font-size: 9.5pt; }}
  th {{ background: #f2f3ef; }}
  code {{ background: #f4f4f1; padding: 1px 4px; border-radius: 3px; font-size: 9pt; }}
  pre {{ background: #f4f4f1; padding: 10px; border-radius: 6px; white-space: pre-wrap; }}
  blockquote {{ margin: 0.6em 0; padding: 0.2em 0.9em; border-left: 3px solid #ccc; color: #555; }}
  img {{ max-width: 100%; }}
</style>
</head>
<body>
{body}
</body>
</html>"""