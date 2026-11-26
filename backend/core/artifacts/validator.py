"""Deterministic artifact checks.

These checks are intentionally mechanical: structure, citation resolvability
and explicitly requested numeric constraints.  Content quality is still the
user's review responsibility; the checker never claims otherwise.
"""

from __future__ import annotations

import re
from typing import Any

from backend.core.artifacts.model import ArtifactDocument, citations, find_total_score

_PLACEHOLDERS = ("待补充", "TODO", "TBD", "此处省略", "示例文本")
_TOTAL_REQUEST = re.compile(r"总分\s*[:：]?\s*(\d{1,3})")


def validate(
    document: ArtifactDocument,
    *,
    markdown: str,
    instruction: str,
    scope_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def add(check_id: str, label: str, status: str, detail: str) -> None:
        checks.append({"id": check_id, "label": label, "status": status, "detail": detail})

    # -- structure ---------------------------------------------------------
    if document.title.strip():
        add("title", "标题", "pass", document.title)
    else:
        add("title", "标题", "warn", "成果没有标题")

    content_sections = [
        section
        for section in document.iter_sections()
        if section.blocks or section.children
    ]
    if content_sections:
        add("structure", "章节结构", "pass", f"{len(content_sections)} 个章节")
    else:
        add("structure", "章节结构", "fail", "成果没有可用章节")

    empty_titles = [section.title for section in content_sections if not section.title.strip()]
    if empty_titles:
        add("empty_titles", "空标题", "warn", f"{len(empty_titles)} 个章节标题为空")
    else:
        add("empty_titles", "空标题", "pass", "所有章节均有标题")

    text = document.plain_text()
    found_placeholders = [word for word in _PLACEHOLDERS if word in text]
    if found_placeholders:
        add("placeholders", "占位内容", "warn", f"检测到占位词：{'、'.join(found_placeholders)}")
    else:
        add("placeholders", "占位内容", "pass", "未检测到常见占位词")

    # -- citations ---------------------------------------------------------
    cites = citations(markdown)
    if not cites:
        add("citations", "引用", "warn", "成果中没有可识别的来源引用（（《文档》第 N 页）格式）")
    else:
        scope_titles = [
            str(item.get("title") or "") for item in scope_inputs if item.get("title")
        ]
        unresolved = []
        for cite in cites:
            name = cite["document"]
            if not any(name in title or title in name for title in scope_titles):
                unresolved.append(name)
        if unresolved:
            add(
                "citations",
                "引用",
                "warn",
                f"{len(unresolved)} 个引用与任务范围文档标题不匹配：{'、'.join(sorted(set(unresolved))[:5])}",
            )
        else:
            add("citations", "引用", "pass", f"{len(cites)} 个引用均可在任务范围内找到对应文档")

    # -- explicit numeric requirements ------------------------------------
    requested = _TOTAL_REQUEST.search(instruction or "")
    if requested:
        expected = int(requested.group(1))
        found = find_total_score(markdown)
        if not found:
            add("total_score", "总分核对", "warn", f"任务要求总分 {expected}，成果中未找到明确总分")
        elif expected in found:
            add("total_score", "总分核对", "pass", f"成果总分与要求一致（{expected}）")
        else:
            add(
                "total_score",
                "总分核对",
                "fail",
                f"任务要求总分 {expected}，成果中出现的总分为 {found}",
            )

    statuses = {check["status"] for check in checks}
    overall = "fail" if "fail" in statuses else ("warn" if "warn" in statuses else "pass")
    return {"overall": overall, "checks": checks}