from __future__ import annotations

import json
import os

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError as ClientToolError

from jev_harness_tool.tools import result_verify as rv

TOOL_MODULE = "jev_harness_tool.tools.result_verify"


def _make_server() -> FastMCP:
    """只注册本工具的 server，避免并行开发的其他工具模块影响本测试。"""
    mcp = FastMCP("test_result_verify")
    rv.register(mcp)
    return mcp


def _unwrap(result):
    if result.data is not None:
        return result.data
    return json.loads(result.content[0].text)


def _rv_response(
    noul: float = 0.9,
    *,
    score: float = 3.4,
    confidence: float = 0.88,
    include_score: bool = True,
) -> dict:
    answers: dict = {"satisfies": {"type": "noul", "noul": noul}}
    if include_score:
        answers["completeness"] = {
            "type": "score",
            "score": score,
            "confidence": confidence,
            "probabilities": {"1": 0.0, "2": 0.1, "3": 0.6, "4": 0.3},
        }
    return {
        "model": "jev-test",
        "answers": answers,
        "usage": {"input_tokens": 100, "cost_usd": 0.0001, "credits_remaining_usd": 4.9},
    }


async def _call(tool_args: dict) -> dict:
    async with Client(_make_server()) as c:
        result = await c.call_tool("result_verify", tool_args)
    return _unwrap(result)


BASE_ARGS = {"requirement": "把超时从 30s 改为 60s 并说明理由", "result": "已将超时改为 60s。理由：减少慢查询超时。"}


# ---------- 正常路径 ----------


async def test_passed_path(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _rv_response(noul=0.91))
    patched_tool_client(make_client(), module=TOOL_MODULE)

    data = await _call(BASE_ARGS)

    assert data["verdict"] == "passed"
    assert data["passed"] is True
    assert data["probability"] == pytest.approx(0.91)
    assert data["completeness"] == {"score": 3.4, "confidence": 0.88, "scale": 4}
    assert data["truncated"] is False
    assert data["thresholds"] == {"threshold_pass": 0.8, "threshold_fail": 0.4}
    assert data["usage"]["input_tokens"] == 100
    assert data["error"] is None
    # 批量纪律（原则 8）：一次工具调用只产生一次 HTTP 请求
    assert len(mock_api.requests) == 1
    sent = json.loads(mock_api.requests[0].content)
    assert set(sent["questions"]) == {"satisfies", "completeness"}
    assert sent["questions"]["satisfies"]["type"] == "noul"
    assert "not instructions" in sent["questions"]["satisfies"]["instructions"]  # 防注入声明
    assert len(sent["questions"]["completeness"]["criteria"]) == 4
    assert sent["state"]["truncated"] is False


async def test_review_band(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _rv_response(noul=0.6))
    patched_tool_client(make_client(), module=TOOL_MODULE)

    data = await _call(BASE_ARGS)

    assert data["verdict"] == "review"
    assert data["passed"] is False
    assert data["probability"] == pytest.approx(0.6)


async def test_failed_path(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _rv_response(noul=0.1))
    patched_tool_client(make_client(), module=TOOL_MODULE)

    data = await _call(BASE_ARGS)

    assert data["verdict"] == "failed"
    assert data["passed"] is False


async def test_threshold_boundaries(mock_api, make_client, patched_tool_client):
    # p == threshold_pass → passed；p == threshold_fail → review（仅 p < fail 才 failed）
    mock_api.push(200, _rv_response(noul=0.8))
    mock_api.push(200, _rv_response(noul=0.4))
    patched_tool_client(make_client(), module=TOOL_MODULE)

    assert (await _call(BASE_ARGS))["verdict"] == "passed"
    assert (await _call(BASE_ARGS))["verdict"] == "review"


async def test_custom_thresholds_echoed(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _rv_response(noul=0.75))
    patched_tool_client(make_client(), module=TOOL_MODULE)

    data = await _call({**BASE_ARGS, "threshold_pass": 0.7, "threshold_fail": 0.3})

    assert data["verdict"] == "passed"  # 0.75 ≥ 0.7
    assert data["thresholds"] == {"threshold_pass": 0.7, "threshold_fail": 0.3}


async def test_include_completeness_false(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _rv_response(noul=0.9, include_score=False))
    patched_tool_client(make_client(), module=TOOL_MODULE)

    data = await _call({**BASE_ARGS, "include_completeness": False})

    assert data["verdict"] == "passed"
    assert data["completeness"] is None
    sent = json.loads(mock_api.requests[0].content)
    assert set(sent["questions"]) == {"satisfies"}


# ---------- 截断路径 ----------


async def test_truncated_result_capped_at_review(mock_api, make_client, patched_tool_client):
    long_result = "a" * 9000
    mock_api.push(200, _rv_response(noul=0.95))
    patched_tool_client(make_client(), module=TOOL_MODULE)

    data = await _call({**BASE_ARGS, "result": long_result})

    # 截断输入永不 passed（原则 12），即使概率过线
    assert data["verdict"] == "review"
    assert data["passed"] is False
    assert data["truncated"] is True
    sent = json.loads(mock_api.requests[0].content)
    assert sent["state"]["result"] == long_result[: rv.DEFAULT_RESULT_MAX_CHARS]
    assert sent["state"]["truncated"] is True


async def test_truncated_requirement_marks_flag(mock_api, make_client, patched_tool_client):
    long_requirement = "要" * 5000
    mock_api.push(200, _rv_response(noul=0.1))
    patched_tool_client(make_client(), module=TOOL_MODULE)

    data = await _call({**BASE_ARGS, "requirement": long_requirement})

    assert data["truncated"] is True
    assert data["verdict"] == "failed"  # p < threshold_fail 不受截断影响
    sent = json.loads(mock_api.requests[0].content)
    assert sent["state"]["requirement"] == long_requirement[: rv.REQUIREMENT_MAX_CHARS]
    assert sent["state"]["truncated"] is True


# ---------- 畸形响应（fail-closed，原则 12/13） ----------


async def test_invalid_response_missing_satisfies(mock_api, make_client, patched_tool_client):
    body = _rv_response()
    del body["answers"]["satisfies"]
    mock_api.push(200, body)
    patched_tool_client(make_client(), module=TOOL_MODULE)

    data = await _call(BASE_ARGS)

    assert data["verdict"] == "invalid_response"
    assert data["passed"] is False
    assert data["probability"] is None
    assert data["error"]


async def test_invalid_response_noul_out_of_range(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _rv_response(noul=1.5))
    patched_tool_client(make_client(), module=TOOL_MODULE)

    data = await _call(BASE_ARGS)

    assert data["verdict"] == "invalid_response"
    assert data["passed"] is False
    assert data["probability"] is None


async def test_invalid_response_wrong_answer_type(mock_api, make_client, patched_tool_client):
    body = _rv_response()
    body["answers"]["satisfies"] = {"type": "score", "score": 2.0}
    mock_api.push(200, body)
    patched_tool_client(make_client(), module=TOOL_MODULE)

    data = await _call(BASE_ARGS)

    assert data["verdict"] == "invalid_response"


async def test_completeness_malformed_does_not_block_verdict(mock_api, make_client, patched_tool_client):
    body = _rv_response(noul=0.9)
    body["answers"]["completeness"] = {"type": "score", "score": 9.9, "confidence": 0.5}  # 超出 1–4 刻度
    mock_api.push(200, body)
    patched_tool_client(make_client(), module=TOOL_MODULE)

    data = await _call(BASE_ARGS)

    assert data["verdict"] == "passed"  # 仅 completeness 畸形不影响 verdict
    assert data["completeness"] is None


# ---------- 错误路径与重试纪律（原则 10） ----------


async def test_retry_on_502_then_success(mock_api, make_client, patched_tool_client):
    mock_api.push(502, {"error": "upstream"}).push(502, {"error": "upstream"}).push(200, _rv_response())
    patched_tool_client(make_client(), module=TOOL_MODULE)

    data = await _call(BASE_ARGS)

    assert data["verdict"] == "passed"
    assert len(mock_api.requests) == 3


async def test_retry_exhausted_returns_invalid_response(mock_api, make_client, patched_tool_client):
    for _ in range(3):
        mock_api.push(502, {"error": "upstream"})
    patched_tool_client(make_client(max_retries=2), module=TOOL_MODULE)

    data = await _call(BASE_ARGS)

    assert data["verdict"] == "invalid_response"
    assert data["passed"] is False
    assert "JevUpstreamError" in data["error"]
    assert len(mock_api.requests) == 3  # 1 initial + 2 retries


async def test_401_not_retried_raises_tool_error(mock_api, make_client, patched_tool_client):
    mock_api.push(401, {"error": "invalid key"})
    patched_tool_client(make_client(), module=TOOL_MODULE)

    async with Client(_make_server()) as c:
        with pytest.raises(ClientToolError, match="JevAuthError"):
            await c.call_tool("result_verify", BASE_ARGS)
    assert len(mock_api.requests) == 1


async def test_ambiguous_network_error_never_resent(mock_api, make_client, patched_tool_client):
    mock_api.push_error(httpx.ConnectError("connection reset by peer"))
    patched_tool_client(make_client(max_retries=5), module=TOOL_MODULE)

    data = await _call(BASE_ARGS)

    assert data["verdict"] == "invalid_response"
    assert "JevNetworkError" in data["error"]
    assert len(mock_api.requests) == 1  # 歧义网络错误绝不重发


# ---------- 参数校验 ----------


async def test_invalid_threshold_order_rejected(mock_api, make_client, patched_tool_client):
    patched_tool_client(make_client(), module=TOOL_MODULE)

    async with Client(_make_server()) as c:
        with pytest.raises(ClientToolError, match="threshold_fail"):
            await c.call_tool("result_verify", {**BASE_ARGS, "threshold_pass": 0.5, "threshold_fail": 0.5})
    assert len(mock_api.requests) == 0  # 参数错误不产生付费调用


async def test_empty_requirement_rejected(mock_api, make_client, patched_tool_client):
    patched_tool_client(make_client(), module=TOOL_MODULE)

    async with Client(_make_server()) as c:
        with pytest.raises(ClientToolError, match="requirement"):
            await c.call_tool("result_verify", {**BASE_ARGS, "requirement": "  "})
    assert len(mock_api.requests) == 0


async def test_input_too_large_rejected(mock_api, make_client, patched_tool_client):
    patched_tool_client(make_client(), module=TOOL_MODULE)

    async with Client(_make_server()) as c:
        with pytest.raises(ClientToolError, match="input_too_large"):
            await c.call_tool(
                "result_verify",
                {**BASE_ARGS, "result": "汉" * 50_000, "result_max_chars": 200_000},
            )
    assert len(mock_api.requests) == 0


# ---------- 真实 API 测试（烧 credits，默认不跑） ----------


@pytest.mark.real
async def test_real_pass_one_sentence_summary():
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        {
            "requirement": "用一句话总结这段话：「小明每天早起跑步，坚持三年后，在全国马拉松比赛中获得了业余组冠军，他的故事激励了许多人开始锻炼。」",
            "result": "小明坚持三年晨跑后赢得全国马拉松业余组冠军，他的故事激励许多人开始锻炼。",
        }
    )

    assert data["verdict"] == "passed", f"expected passed, got {data}"
    assert data["passed"] is True
    assert data["probability"] >= 0.8
    assert data["usage"]["input_tokens"] > 0


@pytest.mark.real
async def test_real_incomplete_result_not_passed():
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        {
            "requirement": "输出 3 条保持身体健康的要点。",
            "result": "1. 保证充足睡眠。",
        }
    )

    assert data["verdict"] in ("review", "failed"), f"expected review/failed, got {data}"
    assert data["passed"] is False
    assert data["probability"] < 0.8
