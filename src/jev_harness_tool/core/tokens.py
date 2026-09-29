from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

TOTAL_TOKEN_BUDGET = 64_000
SINGLE_TOKEN_BUDGET = 32_000

_ASCII_WORD_CHARS_PER_TOKEN = 6.0
_DIGIT_TOKENS_PER_CHAR = 0.5
_OTHER_TOKENS_PER_CHAR = 0.9

T = TypeVar("T")


class TokenBudgetError(ValueError):
    """单个 item + state 已超出单问 32k 预算，分批无解；调用方必须裁剪后重试。"""


def estimate_tokens(obj: Any) -> int:
    """保守估算（原则 9：按字符类别计价，故意高估留安全边际）。

    ASCII 字母每 6 字符 1 token，数字每字符 0.5，其他（含 JSON 结构字符、
    空白、CJK）每字符 0.9。非字符串输入先 JSON 序列化。
    """
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    tokens = 0.0
    for ch in text:
        if ch.isascii() and ch.isalpha():
            tokens += 1.0 / _ASCII_WORD_CHARS_PER_TOKEN
        elif ch.isdigit():
            tokens += _DIGIT_TOKENS_PER_CHAR
        else:
            tokens += _OTHER_TOKENS_PER_CHAR
    return math.ceil(tokens)


@dataclass(frozen=True)
class BudgetReport:
    state_tokens: int
    question_tokens: dict[str, int]
    total_tokens: int
    worst_single_tokens: int
    fits_total: bool
    fits_single: bool

    @property
    def fits(self) -> bool:
        return self.fits_total and self.fits_single


def budget_report(state: Any, questions: Mapping[str, Any]) -> BudgetReport:
    state_tokens = estimate_tokens(state)
    per_question = {name: estimate_tokens(q) for name, q in questions.items()}
    total = state_tokens + sum(per_question.values())
    worst_single = state_tokens + max(per_question.values(), default=0)
    return BudgetReport(
        state_tokens=state_tokens,
        question_tokens=per_question,
        total_tokens=total,
        worst_single_tokens=worst_single,
        fits_total=total <= TOTAL_TOKEN_BUDGET,
        fits_single=worst_single <= SINGLE_TOKEN_BUDGET,
    )


def fits_budget(state: Any, questions: Mapping[str, Any]) -> bool:
    return budget_report(state, questions).fits


def plan_batches(
    items: Sequence[T],
    state: Any,
    make_question: Callable[[T], Mapping[str, Any]],
) -> list[list[T]]:
    """按 64k 总预算反推分批（原则 9）。每批随 state 重发，调用方负责多批并发。

    make_question(item) 返回该 item 对应的单个 question dict，用于估算其 token 成本。
    单个 item + state 超出 32k 单问预算时抛 TokenBudgetError（fail-closed，不静默截断）。
    """
    state_tokens = estimate_tokens(state)
    batches: list[list[T]] = []
    current: list[T] = []
    current_tokens = state_tokens
    for item in items:
        question_tokens = estimate_tokens(make_question(item))
        if state_tokens + question_tokens > SINGLE_TOKEN_BUDGET:
            raise TokenBudgetError(
                f"single item ({question_tokens} est. tokens) + state ({state_tokens}) "
                f"exceeds the {SINGLE_TOKEN_BUDGET} single-question budget; truncate before batching"
            )
        if current and current_tokens + question_tokens > TOTAL_TOKEN_BUDGET:
            batches.append(current)
            current = []
            current_tokens = state_tokens
        current.append(item)
        current_tokens += question_tokens
    if current:
        batches.append(current)
    return batches
