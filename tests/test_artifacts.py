from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.core.artifacts.model import (
    citations,
    extract_section,
    find_total_score,
    parse_artifact,
    replace_section,
    strip_citations,
)
from backend.core.artifacts.validator import validate

MARKDOWN = """# 教学大纲：中国古代史

## 第一单元 先秦时期

- 了解中华文明起源
- 理解分封制与宗法制

| 课时 | 内容 | 重点 |
| --- | --- | --- |
| 2 | 秦统一 | 郡县制 |

### 第 1 课 早期国家

秦始皇统一六国后推行郡县制（《中国历史》第 12 页）。

总分：100 分
"""


def test_artifact_structure_and_helpers() -> None:
    document = parse_artifact(MARKDOWN)
    assert document.title == "教学大纲：中国古代史"
    assert document.sections[0].title == "第一单元 先秦时期"
    assert document.sections[0].children[0].title == "第 1 课 早期国家"

    kinds = {block.kind for section in document.iter_sections() for block in section.blocks}
    assert {"list", "table", "paragraph"} <= kinds

    cites = citations(MARKDOWN)
    assert cites == [{"document": "中国历史", "page": 12}]
    assert "《中国历史》" not in strip_citations(MARKDOWN)
    assert find_total_score(MARKDOWN) == [100]

    level, lines = extract_section(MARKDOWN, "第 1 课 早期国家")
    assert level == 3
    assert any("郡县制" in line for line in lines)


def test_replace_section_keeps_other_chapters() -> None:
    new_markdown = replace_section(
        MARKDOWN,
        "第一单元 先秦时期",
        "## 第一单元 先秦时期\n\n改写后的单元导语。",
    )
    assert "改写后的单元导语" in new_markdown
    assert "第 1 课 早期国家" in new_markdown
    assert "总分：100 分" in new_markdown
    assert "了解中华文明起源" not in new_markdown


def test_validator_reports_citation_and_total_mismatch() -> None:
    document = parse_artifact(MARKDOWN)
    report = validate(
        document,
        markdown=MARKDOWN,
        instruction="生成一份试卷，总分 120 分",
        scope_inputs=[{"title": "中国历史"}],
    )
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["citations"]["status"] == "pass"
    assert checks["total_score"]["status"] == "fail"
    assert report["overall"] == "fail"

    report = validate(
        document,
        markdown=MARKDOWN.replace("《中国历史》", "《不存在》"),
        instruction="总结",
        scope_inputs=[{"title": "中国历史"}],
    )
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["citations"]["status"] == "warn"


@pytest.mark.asyncio
async def test_exports_markdown_docx_json_pdf(runtime) -> None:
    service = runtime.artifacts
    assert service is not None
    artifact = service.create(title="教学大纲测试", markdown=MARKDOWN)
    artifact_id = artifact["id"]

    md = await service.export(artifact_id, format="md")
    assert Path(md["path"]).read_text(encoding="utf-8") == MARKDOWN

    json_export = await service.export(artifact_id, format="json")
    payload = json.loads(Path(json_export["path"]).read_text(encoding="utf-8"))
    assert payload["structure"]["title"] == "教学大纲：中国古代史"
    assert payload["markdown"] == MARKDOWN

    docx_export = await service.export(artifact_id, format="docx")
    from docx import Document

    document = Document(docx_export["path"])
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    for table in document.tables:
        for row in table.rows:
            text += "\n" + " | ".join(cell.text for cell in row.cells)
    assert "教学大纲：中国古代史" in text
    assert "郡县制" in text
    assert "第 1 课 早期国家" in text

    pdf_export = await service.export(artifact_id, format="pdf")
    pdf_path = Path(pdf_export["path"])
    assert pdf_path.is_file() and pdf_path.stat().st_size > 2000
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        text = "\n".join(pdf[index].get_textpage().get_text_range() for index in range(len(pdf)))
    finally:
        pdf.close()
    assert "教学大纲" in text
    assert "郡县制" in text

    # Citations can be removed from the print copy while the stored version keeps them.
    clean = await service.export(artifact_id, format="pdf", include_citations=False)
    assert clean["filename"].endswith(".pdf")


@pytest.mark.asyncio
async def test_regenerate_only_target_section(runtime) -> None:
    service = runtime.artifacts
    assert service is not None
    from backend.core.config import AppConfig, EndpointConfig

    runtime.save_config(
        AppConfig(
            endpoints=[
                EndpointConfig(
                    id="llm",
                    kind="llm",
                    provider="openai_compat",
                    base_url="https://models.test/v1",
                    model="fake-chat",
                    timeout_s=10,
                )
            ],
            default_llm="llm",
        )
    )
    artifact = service.create(title="教学大纲测试", markdown=MARKDOWN)

    import httpx
    import respx

    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://models.test/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "model": "fake-chat",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "## 第一单元 先秦时期\n\n重写后的单元内容（《中国历史》第 3 页）。",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 20},
                },
            )
        )
        updated = await service.regenerate_section(
            artifact["id"],
            heading="第一单元 先秦时期",
            instruction="压缩为一段导语",
        )

    markdown = updated["version"]["markdown"]
    assert "重写后的单元内容" in markdown
    assert "第 1 课 早期国家" in markdown
    assert "总分：100 分" in markdown
    assert updated["version"]["version"] == 2


def test_artifact_api_flow(client) -> None:
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    artifact = runtime.artifacts.create(title="接口成果", markdown=MARKDOWN)

    listing = client.get("/api/artifacts").json()["items"]
    assert any(item["id"] == artifact["id"] for item in listing)

    detail = client.get(f"/api/artifacts/{artifact['id']}").json()
    assert detail["validation"]["overall"] in {"pass", "warn", "fail"}
    assert detail["structure"]["title"] == "教学大纲：中国古代史"

    edited = client.put(
        f"/api/artifacts/{artifact['id']}", json={"markdown": MARKDOWN + "\n补充说明。\n"}
    )
    assert edited.status_code == 200
    assert edited.json()["version"]["version"] == 2

    exported = client.post(
        f"/api/artifacts/{artifact['id']}/export", json={"format": "md"}
    )
    assert exported.status_code == 200, exported.text
    path = Path(exported.json()["path"])
    assert path.is_file()

    download = client.get(f"/api/artifacts/{artifact['id']}/download/md")
    assert download.status_code == 200
    assert "补充说明" in download.text

def test_pdf_cli_fallback_renders(tmp_path: Path) -> None:
    from backend.core.artifacts.exporter import _HTML_TEMPLATE, _render_pdf_browser_cli

    html = _HTML_TEMPLATE.format(
        body="<h1>边缘渲染测试</h1><p>这是可搜索的中文文本层。</p>"
    )
    target = tmp_path / "edge.pdf"
    ok = _render_pdf_browser_cli(html, target)
    if not ok:
        pytest.skip("当前环境没有可用的 Edge/Chrome，无法验证命令行 PDF 回退")

    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(target))
    try:
        text = pdf[0].get_textpage().get_text_range()
    finally:
        pdf.close()
    assert "边缘渲染测试" in text
