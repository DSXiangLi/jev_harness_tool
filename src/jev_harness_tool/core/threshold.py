from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

Verdict = Literal["accept", "review", "reject"]


def margin(probabilities: Mapping[str, float]) -> float:
    """冠亚军概率差。空分布返回 0.0，单选项分布返回该项概率。"""
    values = sorted(probabilities.values(), reverse=True)
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return values[0] - values[1]


def top_choice(probabilities: Mapping[str, float]) -> tuple[str, float] | None:
    if not probabilities:
        return None
    winner = max(probabilities, key=lambda k: probabilities[k])
    return winner, probabilities[winner]


def choice_verdict(
    probabilities: Mapping[str, float] | None,
    threshold: float,
    min_margin: float,
) -> Verdict:
    """原则 5：top 概率 ≥ threshold 且冠亚军差 ≥ min_margin 才 accept，否则 review。

    概率缺失/畸形时 fail-closed 返回 review（原则 12：confidence 未知视为不满足任何阈值）。
    """
    if not probabilities:
        return "review"
    top = top_choice(probabilities)
    if top is None:
        return "review"
    _, p_top = top
    if p_top >= threshold and margin(probabilities) >= min_margin:
        return "accept"
    return "review"


def noul_verdict(
    probability: float | None,
    pass_threshold: float,
    fail_threshold: float | None = None,
) -> Verdict:
    """noul 门控。p ≥ pass_threshold → accept；给了 fail_threshold 时 p ≤ fail → reject，
    中间为 review 带；不给 fail_threshold 时未过线即 reject。
    概率缺失或越界 → review（fail-closed）。
    """
    if (
        probability is None
        or isinstance(probability, bool)
        or not isinstance(probability, (int, float))
        or not 0.0 <= probability <= 1.0
    ):
        return "review"
    if probability >= pass_threshold:
        return "accept"
    if fail_threshold is None:
        return "reject"
    if probability <= fail_threshold:
        return "reject"
    return "review"
