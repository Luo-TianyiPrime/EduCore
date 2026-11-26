"""Generate synthetic teaching fixtures for development and tests.

Everything produced here is synthetic and clearly labelled as such. It is not
real textbook or exam content and must never be bundled with the product.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FONT_CANDIDATES = [
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def find_font() -> str | None:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def write_markdown(target: Path) -> None:
    (target / "中国历史示例教材.md").write_text(
        """# 第一单元 中华文明的起源与早期国家

> 合成示例资料，仅用于 EduCore 功能验证，不是真实教材内容。

## 第 1 课 中华文明的起源

中华文明在多个区域起源，形成了各具特色的早期文化。农业的发展使定居生活成为可能。

- 旧石器时代以打制石器为主
- 新石器时代出现磨制石器与陶器
- 长江流域与黄河流域是重要的文明发源地

## 第 2 课 早期国家的建立

夏商周时期，王权与礼制逐步形成。西周实行分封制与宗法制，对后世影响深远。

| 时期 | 制度 | 主要特点 |
| --- | --- | --- |
| 夏 | 王位世袭 | 家天下取代公天下 |
| 商 | 内外服制 | 王畿与方国并存 |
| 西周 | 分封制 | 以血缘为纽带 |

## 第 3 课 春秋战国的社会变革

铁犁牛耕推动生产力发展，各国变法图强。百家争鸣是这一时期思想文化繁荣的体现。

1. 经济：井田制瓦解，土地私有出现
2. 政治：分封制崩溃，中央集权萌芽
3. 思想：儒家、道家、法家等著述流传
""",
        encoding="utf-8",
    )

    (target / "地理复习提纲.md").write_text(
        """# 自然地理复习提纲

> 合成示例资料，仅用于功能验证。

## 一、地球运动

### 1. 地球自转

地球自转方向为自西向东，产生昼夜交替现象。自转线速度由赤道向两极递减。

### 2. 地球公转

地球公转导致太阳直射点回归运动，形成四季更替与五带划分。

## 二、大气运动

热力环流是大气运动最简单的形式。近地面受热地区空气上升，冷却地区空气下沉。

- 海陆风
- 山谷风
- 城市热岛环流

## 三、水循环

水循环联系海洋、陆地与大气，促进能量交换和物质迁移。
""",
        encoding="utf-8",
    )


def write_ocr_json(target: Path) -> None:
    payload = {
        "title": "扫描历史试卷（合成 OCR 示例）",
        "pages": [
            {
                "page": 1,
                "text": "一、选择题\n1. 秦朝统一六国后推行的制度是（ ）\nA. 分封制 B. 郡县制 C. 行省制 D. 内阁制",
                "blocks": [
                    {"text": "一、选择题", "bbox": [72, 90, 200, 110], "confidence": 0.99},
                    {
                        "text": "1. 秦朝统一六国后推行的制度是（ ）",
                        "bbox": [72, 120, 480, 142],
                        "confidence": 0.97,
                    },
                    {
                        "text": "A. 分封制 B. 郡县制 C. 行省制 D. 内阁制",
                        "bbox": [72, 150, 520, 172],
                        "confidence": 0.96,
                    },
                ],
            },
            {
                "page": 2,
                "text": "二、材料分析题\n阅读材料，回答问题。\n材料：……（合成示例）",
                "blocks": [
                    {"text": "二、材料分析题", "bbox": [72, 90, 220, 110], "confidence": 0.98},
                    {"text": "阅读材料，回答问题。", "bbox": [72, 120, 360, 142], "confidence": 0.97},
                ],
            },
        ],
        "metadata": {"source": "synthetic", "note": "本文件仅用于 EduCore 测试"},
    }
    (target / "扫描试卷示例.ocr.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_pdf(target: Path, pages: int = 4) -> Path | None:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
    except ImportError:
        return None

    font_path = find_font()
    font_name = "Helvetica"
    if font_path:
        try:
            pdfmetrics.registerFont(TTFont("FixtureCJK", font_path))
            font_name = "FixtureCJK"
        except Exception:
            font_name = "Helvetica"

    path = target / "历史真题汇编（合成）.pdf"
    pdf = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    chapters = [
        ("第一章 先秦时期", ["中华文明的起源", "早期国家的政治制度", "春秋战国的社会变革"]),
        ("第二章 秦汉时期", ["秦的统一与中央集权", "汉代的政治与经济", "丝绸之路与中外交流"]),
        ("第三章 隋唐时期", ["科举制度的形成", "唐代的民族关系", "繁荣的对外贸易"]),
        ("第四章 宋元时期", ["宋代商业的发展", "元朝的行省制度", "宋元科技与文化"]),
    ]
    page_no = 0
    for chapter_index, (title, topics) in enumerate(chapters):
        pdf.bookmarkPage(f"ch{chapter_index + 1}")
        pdf.addOutlineEntry(title, f"ch{chapter_index + 1}", level=0)
        pdf.setFont(font_name, 16)
        pdf.drawString(72, height - 80, title)
        pdf.setFont(font_name, 10.5)
        y = height - 115
        for topic in topics:
            pdf.drawString(72, y, f"· {topic}")
            y -= 22
        pdf.drawString(72, y - 10, "本页内容为 EduCore 合成测试资料，不是真实试题。")
        for paragraph in range(3):
            text = (
                f"第 {page_no + 1} 页示例段落 {paragraph + 1}："
                "本段用于验证 PDF 文本层、段落切分与检索定位，内容由程序生成，不代表任何真实教材表述。"
            )
            pdf.drawString(72, y - 50 - paragraph * 40, text[:46])
            pdf.drawString(72, y - 66 - paragraph * 40, text[46:96])
        pdf.showPage()
        page_no += 1

    extra = max(0, pages - len(chapters))
    for index in range(extra):
        pdf.bookmarkPage(f"extra{index}")
        pdf.addOutlineEntry(f"附录 {index + 1}", f"extra{index}", level=1)
        pdf.setFont(font_name, 14)
        pdf.drawString(72, height - 80, f"附录 {index + 1}")
        pdf.setFont(font_name, 10.5)
        for line in range(8):
            pdf.drawString(
                72,
                height - 120 - line * 24,
                f"附录示例文本第 {line + 1} 行：用于规模与性能测试的合成内容。",
            )
        pdf.showPage()

    pdf.save()
    return path


def write_image(target: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return
    image = Image.new("RGB", (900, 500), "white")
    draw = ImageDraw.Draw(image)
    font = None
    font_path = find_font()
    if font_path:
        try:
            font = ImageFont.truetype(font_path, 34)
        except OSError:
            font = None
    draw.text((40, 40), "合成扫描页示例", fill="black", font=font) if font else draw.text(
        (40, 40), "SYNTHETIC SCAN SAMPLE", fill="black"
    )
    draw.text((40, 110), "1. 这是用于 OCR 测试的合成图片。", fill="black", font=font)
    draw.text((40, 170), "2. 图片中的文字不是真实试题。", fill="black", font=font)
    image.save(target / "扫描页示例.png")


def generate(target: Path, pages: int = 4) -> None:
    target.mkdir(parents=True, exist_ok=True)
    write_markdown(target)
    write_ocr_json(target)
    write_pdf(target, pages=pages)
    write_image(target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 EduCore 合成测试资料")
    parser.add_argument("--out", default=".samples", help="输出目录")
    parser.add_argument("--pages", type=int, default=4, help="PDF 页数下限")
    args = parser.parse_args(argv)
    target = Path(args.out).resolve()
    generate(target, pages=args.pages)
    print(f"已生成合成资料: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())