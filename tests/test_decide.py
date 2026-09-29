from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest

from jev_harness_tool.core import (
    JevAuthError,
    JevNetworkError,
    JevPaymentError,
    JevProtocolError,
    JevUpstreamError,
    JevValidationError,
    TokenBudgetError,
    check_answer,
    choice_question,
    choice_verdict,
    estimate_tokens,
    fits_budget,
    margin,
    noul_question,
    noul_verdict,
    plan_batches,
    score_question,
)
from jev_harness_tool.core import tokens as tokens_mod

from conftest import FAKE_API_KEY, make_decide_response

NOUL_Q = {"sky": {"type": "noul", "instructions": "Is the sky blue?"}}
CHOICE_Q = {
    "route": {
        "type": "choice",
        "instructions": "Where should this ticket go?",
        "criteria": {"billing": "payments, refunds", "bug": "product is broken"},
    }
}


def _unwrap(result):
    if result.data is not None:
        return result.data
    return json.loads(result.content[0].text)


# ---------- JevClient mock 测试（不触网） ----------


async def test_decide_roundtrip(mock_api, make_client):
    questions = {**NOUL_Q, **CHOICE_Q}
    mock_api.push(200, make_decide_response(questions))
    client = make_client()

    result = await client.decide("some state", questions)

    assert result.model == "jev-test"
    assert result.answers["sky"]["noul"] == 0.9
    assert result.answers["route"]["choice"] == "billing"
    assert result.usage["input_tokens"] == 42
    assert len(mock_api.requests) == 1
    request = mock_api.requests[0]
    assert request.headers["authorization"] == f"Bearer {FAKE_API_KEY}"
    sent = json.loads(request.content)
    assert sent["model"] == "jev-test"
    assert sent["state"] == "some state"
    assert sent["questions"] == questions


async def test_retry_on_502_then_success(mock_api, make_client):
    mock_api.push(502, {"error": "upstream"}).push(200, make_decide_response(NOUL_Q))
    client = make_client(max_retries=3)

    result = await client.decide("s", NOUL_Q)

    assert result.answers["sky"]["noul"] == 0.9
    assert len(mock_api.requests) == 2


async def test_retry_exhausted_raises_upstream(mock_api, make_client):
    for _ in range(3):
        mock_api.push(502, {"error": "upstream"})
    client = make_client(max_retries=2)

    with pytest.raises(JevUpstreamError):
        await client.decide("s", NOUL_Q)
    assert len(mock_api.requests) == 3  # 1 initial + 2 retries


async def test_401_not_retried(mock_api, make_client):
    mock_api.push(401, {"error": "invalid key"})
    client = make_client()

    with pytest.raises(JevAuthError):
        await client.decide("s", NOUL_Q)
    assert len(mock_api.requests) == 1


async def test_403_not_retried(mock_api, make_client):
    mock_api.push(403, {"error": "account inactive"})
    client = make_client()

    with pytest.raises(JevAuthError):
        await client.decide("s", NOUL_Q)
    assert len(mock_api.requests) == 1


async def test_402_not_retried(mock_api, make_client):
    mock_api.push(402, {"code": "insufficient_credits"})
    client = make_client()

    with pytest.raises(JevPaymentError):
        await client.decide("s", NOUL_Q)
    assert len(mock_api.requests) == 1


async def test_400_raises_validation_with_detail(mock_api, make_client):
    mock_api.push(400, {"error": "questions must be a non-empty map"})
    client = make_client()

    with pytest.raises(JevValidationError) as exc_info:
        await client.decide("s", NOUL_Q)
    assert "questions" in str(exc_info.value)
    assert len(mock_api.requests) == 1


async def test_ambiguous_network_error_never_resent(mock_api, make_client):
    mock_api.push_error(httpx.ConnectError("connection reset by peer"))
    client = make_client(max_retries=5)

    with pytest.raises(JevNetworkError):
        await client.decide("s", NOUL_Q)
    assert len(mock_api.requests) == 1  # 歧义网络错误绝不重发


async def test_timeout_never_resent(mock_api, make_client):
    mock_api.push_error(httpx.ReadTimeout("timed out"))
    client = make_client(max_retries=5)

    with pytest.raises(JevNetworkError):
        await client.decide("s", NOUL_Q)
    assert len(mock_api.requests) == 1


async def test_malformed_envelope_answers_not_dict(mock_api, make_client):
    mock_api.push(200, {"model": "jev-test", "answers": ["nope"], "usage": {"input_tokens": 1}})
    client = make_client()

    with pytest.raises(JevUpstreamError, match="answers"):
        await client.decide("s", NOUL_Q)


async def test_malformed_envelope_usage_invalid(mock_api, make_client):
    mock_api.push(200, {"model": "jev-test", "answers": {}, "usage": {"input_tokens": "many"}})
    client = make_client()

    with pytest.raises(JevUpstreamError, match="usage"):
        await client.decide("s", NOUL_Q)


async def test_malformed_envelope_not_json(mock_api, make_client):
    mock_api.push(200, None)
    client = make_client()

    with pytest.raises(JevUpstreamError):
        await client.decide("s", NOUL_Q)


# ---------- post_readymade 契约（扁平响应，无 answers） ----------


async def test_post_readymade_flat_response(mock_api, make_client):
    body = {"action": "block", "risk": 0.84, "categories": ["destructive"],
            "confidence": 0.6, "usage": {"input_tokens": 10, "cost_usd": 0.0001}}
    mock_api.push(200, body)
    client = make_client()

    data = await client.post_readymade("/api/v1/agent/risk", {"goal": "g"})

    assert data == body
    assert mock_api.requests[0].url.path == "/api/v1/agent/risk"


async def test_post_readymade_usage_optional(mock_api, make_client):
    body = {"action": "allow", "risk": 0.1, "categories": []}  # 无 usage 不报错
    mock_api.push(200, body)
    client = make_client()

    assert await client.post_readymade("/api/v1/agent/risk", {}) == body


async def test_post_readymade_malformed_usage_raises(mock_api, make_client):
    mock_api.push(200, {"action": "allow", "usage": {"input_tokens": "many"}})
    client = make_client()

    with pytest.raises(JevProtocolError):
        await client.post_readymade("/api/v1/agent/risk", {})


async def test_post_readymade_non_dict_raises_protocol_error(mock_api, make_client):
    mock_api.push(200, None)
    client = make_client()

    with pytest.raises(JevProtocolError):
        await client.post_readymade("/api/v1/agent/risk", {})


async def test_post_readymade_retry_and_error_mapping(mock_api, make_client):
    mock_api.push(502, {"error": "x"}).push(200, {"action": "allow"})
    client = make_client()

    assert (await client.post_readymade("/api/v1/agent/risk", {}))["action"] == "allow"
    assert len(mock_api.requests) == 2


async def test_protocol_error_is_upstream_error_subclass(mock_api, make_client):
    mock_api.push(200, None)
    client = make_client()

    with pytest.raises(JevUpstreamError):  # 向后兼容：decide 路径仍可按 JevUpstreamError 捕获
        await client.decide("s", NOUL_Q)


# ---------- get_shared_client 循环感知 ----------


def test_shared_client_recreated_on_loop_change():
    from jev_harness_tool.core.client import get_shared_client, reset_shared_client

    from conftest import make_settings

    async def grab():
        return get_shared_client(settings=make_settings())

    reset_shared_client()
    first = asyncio.run(grab())   # loop A
    second = asyncio.run(grab())  # loop B
    assert first is not second  # 循环更换时重建

    async def grab_twice():
        return get_shared_client(settings=make_settings()), get_shared_client()

    a, b = asyncio.run(grab_twice())
    assert a is b  # 同循环内复用
    reset_shared_client()


# ---------- jev_decide MCP 工具（in-process，mock client 注入） ----------


async def test_tool_roundtrip_via_fastmcp(mock_api, make_client, patched_tool_client):
    from fastmcp import Client

    from jev_harness_tool.server import create_server

    mock_api.push(200, make_decide_response(NOUL_Q))
    patched_tool_client(make_client())

    async with Client(create_server()) as c:
        result = await c.call_tool(
            "jev_decide",
            {"state": "The daytime sky on a clear day.", "questions": NOUL_Q},
        )

    data = _unwrap(result)
    assert data["answers"]["sky"]["noul"] == 0.9
    assert data["usage"]["input_tokens"] == 42
    assert data["model"] == "jev-test"


async def test_tool_surfaces_structured_error(mock_api, make_client, patched_tool_client):
    from fastmcp import Client
    from fastmcp.exceptions import ToolError as ClientToolError

    from jev_harness_tool.server import create_server

    mock_api.push(401, {"error": "invalid key"})
    patched_tool_client(make_client())

    async with Client(create_server()) as c:
        with pytest.raises(ClientToolError, match="JevAuthError"):
            await c.call_tool("jev_decide", {"state": "s", "questions": NOUL_Q})


# ---------- core 单元测试 ----------


def test_question_constructors():
    q = choice_question("Pick one.", {"a": "first option", "b": "second option"})
    assert q == {"type": "choice", "instructions": "Pick one.",
                 "criteria": {"a": "first option", "b": "second option"}}
    s = score_question("Rate it.", ["low", "mid", "high"])
    assert s["type"] == "score" and s["criteria"] == ["low", "mid", "high"]
    n = noul_question("Yes or no?")
    assert n == {"type": "noul", "instructions": "Yes or no?"}


def test_question_constructor_validation():
    with pytest.raises(ValueError):
        choice_question("x", {})
    with pytest.raises(ValueError):
        choice_question("x", {str(i): f"opt {i}" for i in range(256)})
    with pytest.raises(ValueError):
        choice_question("x", {"a": ""})
    with pytest.raises(ValueError):
        score_question("x", ["only one"])
    with pytest.raises(ValueError):
        score_question("x", [str(i) for i in range(11)])
    with pytest.raises(ValueError):
        noul_question("  ")


def test_margin_and_choice_verdict():
    probs = {"a": 0.7, "b": 0.2, "c": 0.1}
    assert margin(probs) == pytest.approx(0.5)
    assert margin({"a": 0.7}) == pytest.approx(0.7)
    assert margin({}) == 0.0
    assert choice_verdict(probs, threshold=0.6, min_margin=0.15) == "accept"
    assert choice_verdict({"a": 0.55, "b": 0.45}, threshold=0.6, min_margin=0.15) == "review"
    assert choice_verdict({"a": 0.65, "b": 0.35}, threshold=0.6, min_margin=0.5) == "review"
    assert choice_verdict(None, threshold=0.6, min_margin=0.15) == "review"  # fail-closed


def test_noul_verdict():
    assert noul_verdict(0.9, pass_threshold=0.8) == "accept"
    assert noul_verdict(0.5, pass_threshold=0.8) == "reject"
    assert noul_verdict(0.5, pass_threshold=0.8, fail_threshold=0.2) == "review"
    assert noul_verdict(0.1, pass_threshold=0.8, fail_threshold=0.2) == "reject"
    assert noul_verdict(None, pass_threshold=0.8) == "review"  # fail-closed
    assert noul_verdict(1.5, pass_threshold=0.8) == "review"  # 越界视为未知


def test_estimate_tokens_conservative():
    assert estimate_tokens("abcdef") == 1  # 6 ASCII 字母 = 1 token
    assert estimate_tokens("12") == 1  # 2 数字 * 0.5
    assert estimate_tokens("天空") == 2  # CJK 每字符 0.9 → ceil
    assert estimate_tokens({"a": "bcdefg"}) >= 1


def test_fits_budget():
    assert fits_budget("small state", NOUL_Q)
    assert not fits_budget("1" * 200_000, NOUL_Q)  # 数字 0.5/字符 → 100k > 64k 总预算


def test_plan_batches_splits_on_total_budget(monkeypatch):
    monkeypatch.setattr(tokens_mod, "TOTAL_TOKEN_BUDGET", 300)
    items = ["item-" + "x" * 600 for _ in range(5)]
    batches = plan_batches(items, "state", lambda it: noul_question(f"Keep {it}?"))
    assert len(batches) > 1
    assert [item for batch in batches for item in batch] == items  # 无丢失、保序


def test_plan_batches_single_item_over_budget_fails_closed():
    huge = "1" * 70_000  # 35k est. tokens > 32k 单问预算
    with pytest.raises(TokenBudgetError):
        plan_batches([huge], "state", lambda it: noul_question(f"Keep {it}?"))


def test_check_answer():
    assert check_answer({"type": "noul", "noul": 0.5}) is None
    assert check_answer({"type": "noul", "noul": 1.2}) is not None
    assert check_answer({"type": "noul"}) is not None
    ok_choice = {"type": "choice", "choice": "a", "probabilities": {"a": 0.6, "b": 0.4}}
    assert check_answer(ok_choice) is None
    assert check_answer({**ok_choice, "choice": "b"}) is not None  # 非 argmax
    assert check_answer({**ok_choice, "choice": "zzz"}) is not None
    assert check_answer({"type": "choice", "choice": "a",
                         "probabilities": {"a": 1.7}}) is not None
    assert check_answer({"type": "score", "score": 2.0}) is None
    assert check_answer({"type": "score"}) is not None
    assert check_answer({"type": "mystery"}) is not None
    assert check_answer("not a dict") is not None


# ---------- 真实 API 测试（烧 credits，默认不跑） ----------


@pytest.mark.real
async def test_real_jev_decide_tool():
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")
    from fastmcp import Client

    from jev_harness_tool.server import create_server

    async with Client(create_server()) as c:
        result = await c.call_tool(
            "jev_decide",
            {
                "state": "A question about the daytime sky on a clear day.",
                "questions": {"sky": {"type": "noul", "instructions": "Is the sky blue?"}},
            },
        )

    data = _unwrap(result)
    assert "sky" in data["answers"]
    noul = data["answers"]["sky"]["noul"]
    assert isinstance(noul, (int, float)) and 0.0 <= noul <= 1.0
    assert data["usage"]["input_tokens"] > 0
