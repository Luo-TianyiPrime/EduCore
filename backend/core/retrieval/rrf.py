"""Reciprocal Rank Fusion for heterogeneous candidate lists."""

from __future__ import annotations

from collections.abc import Sequence


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[str]],
    *,
    k: int = 60,
) -> list[tuple[str, float]]:
    """Fuse rank-only lists.  Scores are never compared across models."""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


def merge_ranked(
    primary: Sequence[str],
    secondary: Sequence[str],
    *,
    k: int = 60,
) -> list[str]:
    fused = reciprocal_rank_fusion([primary, secondary], k=k)
    return [item_id for item_id, _score in fused]