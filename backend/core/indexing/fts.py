"""Chinese-aware FTS5 tokenisation helpers.

SQLite has no built-in Chinese tokenizer, so text is pre-segmented with jieba
and stored space-separated in an FTS5 column.  Queries are segmented with the
same algorithm.  A trigram index is maintained alongside for substring and
OCR-noise matching, but the primary ranking path uses the segmented index.
"""

from __future__ import annotations

import logging
import re
import threading

logger = logging.getLogger(__name__)

_TERM_RE = re.compile(r"[\w\u3400-\u4dbf\u4e00-\u9fff]+", re.UNICODE)
_lock = threading.Lock()
_jieba = None


def _load_jieba():
    global _jieba
    if _jieba is None:
        with _lock:
            if _jieba is None:
                import jieba

                jieba.setLogLevel(logging.WARNING)
                _jieba = jieba
    return _jieba


def tokenize(text: str, *, max_terms: int = 4096) -> list[str]:
    if not text:
        return []
    jieba = _load_jieba()
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in jieba.cut_for_search(text):
        token = raw.strip().lower()
        if not token or not _TERM_RE.fullmatch(token):
            continue
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= max_terms:
            break
    return tokens


def tokenized_text(text: str, *, max_terms: int = 4096) -> str:
    return " ".join(tokenize(text, max_terms=max_terms))


def build_match_query(query: str, *, max_terms: int = 32) -> str:
    """Build a safe FTS5 MATCH expression from user text."""
    tokens = tokenize(query, max_terms=max_terms)
    if not tokens:
        return ""
    quoted = []
    for token in tokens:
        escaped = token.replace('"', '""')
        quoted.append(f'"{escaped}"')
    return " OR ".join(quoted)


def is_available() -> bool:
    try:
        _load_jieba()
        return True
    except Exception as exc:  # pragma: no cover - dependency present
        logger.warning("中文分词不可用: %s", exc)
        return False