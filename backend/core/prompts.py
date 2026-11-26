"""Prompt loading from the repository ``prompts/`` directory."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_FALLBACKS = {
    "agent_system.md": "你是 EduCore 文档分析 Agent。只依据工具返回的资料作答，不编造引文或页码。",
    "full_batch.md": "分析给定批次，输出 JSON：summary、key_points、evidence、uncertainties。",
    "synthesis.md": "根据任务与批次笔记生成 Markdown 成果，标注来源，说明资料限制。",
}


def prompts_dir() -> Path:
    import sys

    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / "prompts"


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    path = prompts_dir() / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return _FALLBACKS.get(name, "")