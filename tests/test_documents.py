from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.core.config import ChunkingSettings
from backend.core.documents.markdown import parse_markdown
from backend.core.documents.ocr_json import parse_ocr_json
from backend.core.documents.pdf import parse_pdf
from backend.core.documents.types import ParseError, estimate_tokens
from backend.core.indexing.chunker import BlockInput, chunk_blocks
from backend.core.indexing.fts import build_match_query, tokenize


def test_markdown_structure(tmp_path: Path) -> None:
    path = tmp_path / "lesson.md"
    path.write_text(
        """# 第一单元 古代中国

导语段落，说明本单元范围。

## 第 1 课 中华文明起源

- 要点一
- 要点二

| 时间 | 事件 |
| --- | --- |
| 公元前 221 | 秦统一 |

![示意图](images/map.png)

```python
print("示例")
```
""",
        encoding="utf-8",
    )

    doc = parse_markdown(path)
    assert doc.title == "第一单元 古代中国"
    assert doc.format == "markdown"
    assert len(doc.sections) == 1
    root = doc.sections[0]
    assert root.children[0].title == "第 1 课 中华文明起源"

    kinds = [block.kind for _section, block in doc.iter_blocks()]
    assert "paragraph" in kinds
    assert "list_item" in kinds
    assert "table" in kinds
    assert "code" in kinds

    images = doc.meta["images"]
    assert images and images[0]["src"] == "images/map.png"
    assert "中华文明" in doc.plain_text()


def test_ocr_json_pages_shape(tmp_path: Path) -> None:
    payload = {
        "title": "扫描历史讲义",
        "pages": [
            {
                "page": 1,
                "text": "第一页文字\n第二行",
                "blocks": [
                    {"text": "第一页文字", "bbox": [10, 20, 200, 40], "confidence": 0.98},
                    {"text": "第二行", "bbox": [10, 50, 200, 70]},
                ],
            },
            {"page": 2, "text": "第二页"},
        ],
    }
    path = tmp_path / "ocr.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    doc = parse_ocr_json(path)
    assert doc.title == "扫描历史讲义"
    assert doc.page_count == 2
    assert doc.pages[0].ocr_used is True
    first_blocks = doc.sections[0].blocks
    assert len(first_blocks) == 2
    assert first_blocks[0].bbox == (10.0, 20.0, 200.0, 40.0)
    assert first_blocks[0].meta["confidence"] == 0.98


def test_ocr_json_paddle_shape(tmp_path: Path) -> None:
    payload = {
        "result": {
            "ocrResults": [
                {
                    "prunedResult": {
                        "rec_texts": ["地理试题", "第一题"],
                        "rec_scores": [0.99, 0.95],
                        "rec_polys": [
                            [[10, 10], [100, 10], [100, 30], [10, 30]],
                            [[10, 40], [100, 40], [100, 60], [10, 60]],
                        ],
                    }
                }
            ]
        }
    }
    path = tmp_path / "paddle.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    doc = parse_ocr_json(path)
    assert doc.pages[0].text == "地理试题\n第一题"
    assert len(doc.sections[0].blocks) == 2
    assert doc.sections[0].blocks[0].bbox == (10.0, 10.0, 100.0, 30.0)


def test_ocr_json_rejects_unknown_shape(tmp_path: Path) -> None:
    path = tmp_path / "unknown.json"
    path.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
    with pytest.raises(ParseError):
        parse_ocr_json(path)


def _make_pdf(path: Path) -> None:
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path))
    pdf.bookmarkPage("c1")
    pdf.addOutlineEntry("第一章 绪论", "c1", level=0)
    pdf.setFont("Helvetica", 12)
    pdf.drawString(72, 760, "Chapter One Introduction")
    pdf.drawString(72, 740, "This is the first paragraph of the document.")
    pdf.drawString(72, 700, "A second paragraph appears after a larger gap.")
    pdf.showPage()
    pdf.bookmarkPage("c2")
    pdf.addOutlineEntry("第二章 方法", "c2", level=0)
    pdf.drawString(72, 760, "Chapter Two Methods")
    pdf.drawString(72, 740, "The second page contains methods and results.")
    pdf.save()


def test_pdf_pages_blocks_and_toc(tmp_path: Path) -> None:
    path = tmp_path / "book.pdf"
    _make_pdf(path)

    doc = parse_pdf(path)
    assert doc.page_count == 2
    assert doc.title == "book"
    assert [section.title for section in doc.sections] == ["第一章 绪论", "第二章 方法"]
    assert doc.sections[0].page_start == 1
    assert doc.sections[0].page_end == 1
    assert doc.sections[1].page_start == 2
    assert all(page.has_text for page in doc.pages)

    text = doc.plain_text()
    assert "Chapter One Introduction" in text
    assert "methods and results" in text

    first_blocks = doc.sections[0].blocks
    assert first_blocks
    assert first_blocks[0].page == 1
    assert first_blocks[0].bbox is not None


def test_chunking_respects_boundaries_and_merges_small() -> None:
    settings = ChunkingSettings(target_tokens=30, max_tokens=45, min_tokens=8)
    blocks = [
        BlockInput(id=f"b{i}", text="历史事件的影响需要从政治、经济、文化三个角度分析。" * 3, page=i + 1)
        for i in range(4)
    ]
    chunks = chunk_blocks("第一单元", blocks, settings)
    assert chunks
    assert all(chunk.token_count <= 60 for chunk in chunks)
    assert all(chunk.heading == "第一单元" for chunk in chunks)
    # Every source block is covered; oversized blocks may appear in several chunks.
    covered = {block_id for chunk in chunks for block_id in chunk.block_ids}
    assert covered == {block.id for block in blocks}


def test_chunking_splits_oversized_block() -> None:
    settings = ChunkingSettings(target_tokens=40, max_tokens=50, min_tokens=5)
    long_text = "这是一段很长的材料。" * 60
    chunks = chunk_blocks("材料", [BlockInput(id="b1", text=long_text, page=1)], settings)
    assert len(chunks) > 1
    assert all(chunk.token_count <= 80 for chunk in chunks)
    assert all(chunk.block_ids == ["b1"] for chunk in chunks)


def test_tokenizer_and_query_escape() -> None:
    tokens = tokenize("光合作用与呼吸作用")
    assert "光合作用" in tokens
    query = build_match_query('光合作用 "引号" OR 呼吸作用')
    assert query.startswith('"')
    assert " OR " in query
    # Every fragment is quoted, so user text cannot inject FTS operators.
    for part in query.split(" OR "):
        assert part.startswith('"') and part.endswith('"')

    assert estimate_tokens("光合作用") >= 4
    assert estimate_tokens("photosynthesis is a process") > 0