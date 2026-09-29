from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings

DECIDE_PATH = "/api/v1/decide"
_RETRYABLE_STATUSES = frozenset({408, 429})


class JevError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, detail: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class JevAuthError(JevError):
    """401/403: key missing, invalid, revoked, or account inactive. Never retried."""


class JevPaymentError(JevError):
    """402: insufficient credits. Never retried."""


class JevValidationError(JevError):
    """400: request failed validation; jev's `error` field is carried in `detail`. Never retried."""


class JevUpstreamError(JevError):
    """5xx/408/429 after retries exhausted, unexpected status, or malformed success envelope."""


class JevProtocolError(JevUpstreamError):
    """200 响应体不符合端点契约（非 JSON / 顶层非对象 / envelope 字段非法）。

    工具层可单独捕获本类，将该项标记为 invalid_response（fail-closed），
    与真正的上游故障（重试耗尽的 JevUpstreamError）区分开。
    """


class JevNetworkError(JevError):
    """Ambiguous transport failure (connect reset, TLS, timeout). Never resent: no idempotency key."""


@dataclass(frozen=True)
class DecideResult:
    answers: dict[str, Any]
    usage: dict[str, Any]
    model: str


SleepFn = Callable[[float], Awaitable[None]]


class JevClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._sleep = sleep
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=settings.timeout_s,
            transport=transport,
            headers={"Authorization": f"Bearer {settings.api_key}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def decide(
        self,
        state: str | dict | list,
        questions: Mapping[str, Any],
        model: str | None = None,
    ) -> DecideResult:
        body = {
            "model": model or self._settings.model,
            "state": state,
            "questions": dict(questions),
        }
        data = await self._post(DECIDE_PATH, body)
        return DecideResult(
            answers=data["answers"],
            usage=data["usage"],
            model=data.get("model", ""),
        )

    async def post_readymade(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """POST 现成端点（如 /api/v1/agent/risk、/api/v1/context/filter）。

        契约比 /decide 宽松：200 响应只要求是合法 JSON 对象，且 usage 若存在则合法
        （现成端点响应是扁平的 {action, risk, ...}，没有 answers，也可能没有 model）。
        各字段的语义校验留给调用方（fail-closed 由工具层负责）。
        """
        return await self._post(path, dict(body), parse=_parse_readymade)

    async def _post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        parse: Callable[[httpx.Response], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        parse = parse or _parse_envelope
        attempts = self._settings.max_retries + 1
        for attempt in range(attempts):
            try:
                resp = await self._client.post(path, json=body)
            except httpx.HTTPError as exc:
                raise JevNetworkError(
                    f"ambiguous network error, request NOT resent: {type(exc).__name__}: {exc}"
                ) from exc
            status = resp.status_code
            if status == 200:
                return parse(resp)
            if status in _RETRYABLE_STATUSES or status >= 500:
                if attempt < attempts - 1:
                    base = self._settings.backoff_base_s
                    await self._sleep(base * (2**attempt) + random.uniform(0, base))
                    continue
                raise JevUpstreamError(
                    f"jev upstream error after {attempts} attempts (status {status})",
                    status_code=status,
                    detail=_error_payload(resp),
                )
            _raise_mapped_error(resp)
        raise JevUpstreamError("unreachable retry state")


def _error_payload(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text[:500]


def _raise_mapped_error(resp: httpx.Response) -> None:
    status = resp.status_code
    detail = _error_payload(resp)
    if status in (401, 403):
        raise JevAuthError(f"jev auth error (status {status})", status_code=status, detail=detail)
    if status == 402:
        raise JevPaymentError("jev insufficient credits (402)", status_code=status, detail=detail)
    if status == 400:
        raise JevValidationError(f"jev request validation failed (400): {detail}", status_code=status, detail=detail)
    raise JevUpstreamError(f"unexpected jev status {status}", status_code=status, detail=detail)


def _parse_json_object(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError as exc:
        raise JevProtocolError("malformed envelope: response is not valid JSON") from exc
    if not isinstance(data, dict):
        raise JevProtocolError("malformed envelope: top level is not an object")
    return data


def _validate_usage(usage: Any, *, required: bool) -> None:
    if usage is None:
        if required:
            raise JevProtocolError("malformed envelope: `usage` is missing")
        return
    if not isinstance(usage, dict):
        raise JevProtocolError("malformed envelope: `usage` is not an object")
    if not _is_non_negative_number(usage.get("input_tokens")):
        raise JevProtocolError("malformed envelope: `usage.input_tokens` is missing or invalid")
    cost = usage.get("cost_usd")
    if cost is not None and not _is_non_negative_number(cost):
        raise JevProtocolError("malformed envelope: `usage.cost_usd` is invalid")


def _parse_envelope(resp: httpx.Response) -> dict[str, Any]:
    """/decide 契约：合法 JSON 对象 + answers dict + usage 必填且合法。"""
    data = _parse_json_object(resp)
    if not isinstance(data.get("answers"), dict):
        raise JevProtocolError("malformed envelope: `answers` is missing or not an object")
    _validate_usage(data.get("usage"), required=True)
    return data


def _parse_readymade(resp: httpx.Response) -> dict[str, Any]:
    """现成端点契约：合法 JSON 对象 + usage 如有则合法；语义字段校验留给调用方。"""
    data = _parse_json_object(resp)
    _validate_usage(data.get("usage"), required=False)
    return data


def _is_non_negative_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


def _is_probability(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= v <= 1.0


def _is_probability_map(v: Any) -> bool:
    return isinstance(v, dict) and bool(v) and all(
        isinstance(k, str) and _is_probability(p) for k, p in v.items()
    )


def check_answer(answer: Any) -> str | None:
    """Deep per-answer validation for the tool layer (原则 13).

    Returns None when valid, otherwise a short reason string so the caller can
    mark that single item `invalid_response` without failing the whole batch.
    """
    if not isinstance(answer, dict):
        return "answer is not an object"
    answer_type = answer.get("type")
    if answer_type == "noul":
        if not _is_probability(answer.get("noul")):
            return "noul missing or outside [0,1]"
        return None
    if answer_type == "choice":
        choice = answer.get("choice")
        probs = answer.get("probabilities")
        if not isinstance(choice, str):
            return "choice field missing or not a string"
        if not _is_probability_map(probs):
            return "probabilities missing or contain values outside [0,1]"
        if choice not in probs:
            return "choice is not among probabilities"
        if max(probs, key=lambda k: probs[k]) != choice:
            return "choice is not the argmax of probabilities"
        return None
    if answer_type == "score":
        score = answer.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            return "score missing or not a number"
        probs = answer.get("probabilities")
        if probs is not None and not _is_probability_map(probs):
            return "probabilities contain values outside [0,1]"
        return None
    return f"unknown answer type: {answer_type!r}"


_shared_client: JevClient | None = None
_shared_client_loop: asyncio.AbstractEventLoop | None = None


def get_shared_client(settings: Settings | None = None) -> JevClient:
    """循环感知的共享 JevClient 单例，供所有工具模块的 get_client() 委托。

    httpx.AsyncClient 绑定创建时的事件循环；当检测到当前运行循环与创建时不同
    （如 pytest-asyncio 每个用例一个新循环），丢弃旧 client 并重建。旧 client
    不主动 aclose——其 loop 已更换/关闭，底层连接随之释放。生产环境单循环，
    只创建一次。测试不应复用本单例，而是 monkeypatch 各工具模块的 get_client()。
    """
    global _shared_client, _shared_client_loop
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _shared_client is None or loop is not _shared_client_loop:
        _shared_client = JevClient(settings or Settings.from_env())
        _shared_client_loop = loop
    return _shared_client


def reset_shared_client() -> None:
    """丢弃共享单例（下一个 get_shared_client 调用将重建）。供测试与生命周期管理用。"""
    global _shared_client, _shared_client_loop
    _shared_client = None
    _shared_client_loop = None
