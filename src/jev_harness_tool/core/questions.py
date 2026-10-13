from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MAX_CHOICE_OPTIONS = 255
MIN_SCORE_LEVELS = 2
MAX_SCORE_LEVELS = 10


def choice_question(instructions: str, criteria: Mapping[str, str]) -> dict[str, Any]:
    """单选问题。criteria 为 选项名 → 精确定义 的 map（1–255 项）。"""
    if not instructions or not instructions.strip():
        raise ValueError("instructions must be a non-empty string")
    if not 1 <= len(criteria) <= MAX_CHOICE_OPTIONS:
        raise ValueError(f"choice criteria must have 1..{MAX_CHOICE_OPTIONS} options, got {len(criteria)}")
    for name, definition in criteria.items():
        if not name or not isinstance(name, str):
            raise ValueError("choice option names must be non-empty strings")
        if not definition or not isinstance(definition, str):
            raise ValueError(f"choice option {name!r} must have a non-empty definition")
    return {
        "type": "choice",
        "instructions": instructions,
        "criteria": dict(criteria),
    }


def score_question(instructions: str, criteria: Sequence[str]) -> dict[str, Any]:
    """有序评分问题。criteria 为 2–10 个等级描述（从低到高）。"""
    if not instructions or not instructions.strip():
        raise ValueError("instructions must be a non-empty string")
    if not MIN_SCORE_LEVELS <= len(criteria) <= MAX_SCORE_LEVELS:
        raise ValueError(
            f"score criteria must have {MIN_SCORE_LEVELS}..{MAX_SCORE_LEVELS} levels, got {len(criteria)}"
        )
    for level in criteria:
        if not level or not isinstance(level, str):
            raise ValueError("score levels must be non-empty strings")
    return {
        "type": "score",
        "instructions": instructions,
        "criteria": list(criteria),
    }


def noul_question(
    instructions: str,
    criteria: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """校准是/否问题，返回 0–1 概率。

    criteria（可选）为 ``{"true": yes 定义, "false": no 定义}``，
    给 jev 提供是/否两端的精确定义，提升校准精度。
    """
    if not instructions or not instructions.strip():
        raise ValueError("instructions must be a non-empty string")
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if criteria is not None:
        if set(criteria) != {"true", "false"}:
            raise ValueError("noul criteria must have exactly keys 'true' and 'false'")
        for key, value in criteria.items():
            if not value or not isinstance(value, str):
                raise ValueError(f"noul criteria {key!r} must be a non-empty string")
        q["criteria"] = dict(criteria)
    return q
