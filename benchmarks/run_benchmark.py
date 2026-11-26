"""Synthetic scale benchmark for parsing, indexing and BM25 retrieval.

No LLM is called: this measures the local pipeline only.  Results are written to
``benchmarks/results/`` and summarised in ``docs/PERFORMANCE.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TOPICS = [
    "中华文明的起源与早期国家形态",
    "秦统一与郡县制度",
    "汉代的政治制度与丝绸之路",
    "隋唐科举制度与民族关系",
    "宋元商业发展与行省制度",
    "明清君主专制与对外交流",
    "地球自转与昼夜交替",
    "地球公转与四季变化",
    "大气热力环流与季风",
    "水循环与洋流",
    "细胞结构与光合作用",
    "遗传规律与基因表达",
    "生态系统与物质循环",
    "牛顿运动定律",
    "能量守恒与机械能",
    "化学反应与能量变化",
    "函数与导数",
    "概率统计基础",
    "阅读理解与写作",
    "历史材料分析的方法",
]


def build_pdf(path: Path, pages: int) -> float:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    font_name = "Helvetica"
    for candidate in (
        "C:/Windows/Fonts/simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ):
        if Path(candidate).is_file():
            try:
                pdfmetrics.registerFont(TTFont("BenchCJK", candidate))
                font_name = "BenchCJK"
                break
            except Exception:
                continue

    started = time.perf_counter()
    pdf = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    chapter_every = 25
    for page in range(1, pages + 1):
        if (page - 1) % chapter_every == 0:
            chapter_index = (page - 1) // chapter_every + 1
            key = f"chapter{chapter_index}"
            pdf.bookmarkPage(key)
            pdf.addOutlineEntry(f"第 {chapter_index} 章 合成章节", key, level=0)
        topic = TOPICS[(page - 1) % len(TOPICS)]
        marker = f"PAGEMARK{page}"
        pdf.setFont(font_name, 15)
        pdf.drawString(60, height - 70, f"第 {page} 页 · {topic}")
        pdf.setFont(font_name, 9.5)
        y = height - 105
        lines = [
            f"本页属于专题：{topic}。这是用于规模测试的合成内容（{marker}）。",
            "教学资料通常包含概念解释、史料引用、例题与练习，本段用于模拟连续正文。",
            "检索系统需要在较长文本中定位概念、人物、事件与数据，并返回准确的来源页码。",
            "本段不包含真实教材表述，也不对应任何考试重点，仅用于验证切分与索引性能。",
            "页码、章节与专题在生成时确定，便于核对检索结果能否回跳到正确位置。",
            "教育资料中常见的表格、图片与公式未在此合成文件中模拟，以避免误导评测。",
            "本行用于增加页面文本密度，使切分器产生多个文本块与索引块。",
            "重复出现的通用词会干扰关键词检索，唯一的 PAGEMARK 标记用于核对定位。",
        ]
        for line in lines:
            pdf.drawString(60, y, line)
            y -= 20
        pdf.showPage()
    pdf.save()
    return time.perf_counter() - started


async def run_benchmark(pages: int, output_dir: Path) -> dict:
    from backend.core.runtime import AppRuntime

    work = output_dir / f"pages-{pages}"
    data_dir = work / "data"
    source_dir = work / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = source_dir / "synthetic-textbook.pdf"
    if not pdf_path.is_file():
        generation_seconds = build_pdf(pdf_path, pages)
    else:
        generation_seconds = 0.0

    runtime = AppRuntime.create(data_dir)
    try:
        index = runtime.index
        assert index is not None
        existing = index.sources.list()
        if existing:
            source = existing[0]
        else:
            source = index.add_source(str(source_dir))

        started = time.perf_counter()
        jobs = index.start_scan(source_id=source["id"], force=True)
        while True:
            job = index.get_job(jobs[0]["id"])
            assert job is not None
            if job["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                break
            await asyncio.sleep(0.5)
        index_seconds = time.perf_counter() - started

        documents = index.documents.list(limit=10)
        document = documents[0]
        versions = index.documents.current_versions()
        version = versions[0]

        queries: list[tuple[str, str, int]] = []
        step = max(1, pages // 20)
        for page in range(step, pages + 1, step):
            queries.append((f"PAGEMARK{page}", "marker", page))
        for index_in_topics in range(0, len(TOPICS), 4):
            topic = TOPICS[index_in_topics]
            queries.append((topic, "topic", topic))

        retrieval = runtime.retrieval
        assert retrieval is not None
        latencies: list[float] = []
        marker_hits = 0
        marker_total = 0
        topic_hits = 0
        topic_total = 0
        for query, kind, value in queries:
            started = time.perf_counter()
            result = await retrieval.search(query, top_k=5)
            latencies.append((time.perf_counter() - started) * 1000)
            hits = result["hits"]
            if kind == "marker":
                marker_total += 1
                if hits and hits[0]["page_start"] == value:
                    marker_hits += 1
            else:
                topic_total += 1
                pages_for_topic = {
                    page
                    for page in range(1, pages + 1)
                    if TOPICS[(page - 1) % len(TOPICS)] == value
                }
                if any(hit["page_start"] in pages_for_topic for hit in hits):
                    topic_hits += 1

        latencies.sort()
        p50 = latencies[len(latencies) // 2] if latencies else 0
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else 0
        disk_bytes = sum(
            path.stat().st_size for path in data_dir.rglob("*") if path.is_file()
        )
        result = {
            "pages": pages,
            "generation_seconds": round(generation_seconds, 2),
            "index_seconds": round(index_seconds, 2),
            "index_pages_per_second": round(pages / index_seconds, 1) if index_seconds else None,
            "documents": len(documents),
            "chunks": int(version["chunk_count"]),
            "blocks": int(version["block_count"]),
            "vector_status": version["vector_status"],
            "query_count": len(queries),
            "latency_p50_ms": round(p50, 1),
            "latency_p95_ms": round(p95, 1),
            "marker_top1_accuracy": round(marker_hits / marker_total, 3) if marker_total else None,
            "topic_recall_at_5": round(topic_hits / topic_total, 3) if topic_total else None,
            "data_dir_mb": round(disk_bytes / 1024 / 1024, 1),
            "pdf_size_mb": round(pdf_path.stat().st_size / 1024 / 1024, 1),
            "mode": "BM25 only (no embedding configured)",
        }
    finally:
        await runtime.aclose()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EduCore 合成规模基准")
    parser.add_argument("--pages", type=int, nargs="+", default=[500])
    parser.add_argument("--out", default=str(ROOT / "benchmarks" / "results"))
    args = parser.parse_args(argv)
    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for pages in args.pages:
        print(f"==> {pages} 页基准", flush=True)
        result = asyncio.run(run_benchmark(pages, output_dir))
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    path = output_dir / f"summary-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写入 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())