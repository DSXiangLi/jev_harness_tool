"""effort_route 工具测试。

mock 部分（默认，不触网）：覆盖 docs/design/tools/routing.md §7.3 验收要点。
real 部分（@pytest.mark.real）：真实调用 jev API，验证语义符合预期。
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError as ClientToolError

from jev_harness_tool.server import create_server
from jev_harness_tool.tools import routing

MODULE = "jev_harness_tool.tools.routing"

LEVELS = {
    "off": "无需推理：事实性直答、简单换算、固定格式输出",
    "low": "轻量推理：一两步即可完成的简单推断或改写",
    "medium": "中等推理：需要多步推理、综合多个信息或排查常规问题",
    "high": "深度推理：复杂规划、架构设计、疑难问题排查等高错误代价任务",
}


def _unwrap(result):
    if result.data is not None:
        return result.data
    return json.loads(result.content[0].text)


def _choice_answer(probs: dict[str, float]) -> dict:
    winner = max(probs, key=lambda k: probs[k])
    return {"type": "choice", "choice": winner, "confidence": 0.9, "probabilities": probs}


def _effort_body(probs: dict[str, float]) -> dict:
    return {
        "model": "jev-test",
        "answers": {"effort": _choice_answer(probs)},
        "usage": {"input_tokens": 42, "cost_usd": 0.00001, "credits_remaining_usd": 4.99},
    }


def _effort_probs(**overrides: float) -> dict[str, float]:
    probs = {"off": 0.02, "low": 0.05, "medium": 0.62, "high": 0.30, "uncertain": 0.01}
    probs.update(overrides)
    return probs


async def _call(tool: str, args: dict):
    async with Client(create_server()) as c:
        result = await c.call_tool(tool, args)
    return _unwrap(result)


# ---------- effort_route：正常路径与保守选档（§7.3-1/2/6） ----------


async def test_effort_selected_with_conservative_shift(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _effort_body(_effort_probs()))
    patched_tool_client(make_client(), MODULE)

    data = await _call("effort_route", {"query": "设计一个支持断点续传的分布式任务调度方案", "levels": LEVELS})

    assert data["verdict"] == "selected"
    assert data["effort"] == "medium"
    assert data["action"] == "allow"
    assert data["probability"] == pytest.approx(0.62)
    assert data["margin"] == pytest.approx(0.32)
    # 详设 §4.3 示例 3：剔除 uncertain 重归一化后 CDF ≈ .02/.07/.70/1.00 → conservative = high
    assert data["conservative_level"] == "high"
    assert data["level_order"] == ["off", "low", "medium", "high"]
    assert data["thresholds"] == {"effort_threshold": 0.5, "margin_threshold": 0.15, "safety_threshold": 0.9}
    assert data["stats"]["levels_count"] == 4

    assert len(mock_api.requests) == 1
    sent = json.loads(mock_api.requests[0].content)
    assert set(sent["questions"]) == {"effort"}
    criteria = sent["questions"]["effort"]["criteria"]
    assert set(criteria) == {*LEVELS, "uncertain"}
    assert "none" not in criteria  # effort_route 不注入 none
    assert criteria["uncertain"] == routing.EFFORT_ESCAPE_CRITERIA["uncertain"]
    assert sent["state"]["levels"] == LEVELS


async def test_effort_conservative_level_examples(mock_api, make_client, patched_tool_client):
    """详设 §4.3 示例 1/2：重归一化与 CDF 计算正确。"""
    patched_tool_client(make_client(), MODULE)
    cases = [
        # 示例 1：集中分布无偏移，CDF 刚好触线（0.05/0.20/0.90/1.00）
        ({"off": 0.05, "low": 0.15, "medium": 0.70, "high": 0.10, "uncertain": 0.0}, "medium", "medium"),
        # 示例 2：尾部质量大发生偏移，p=0.55 ≥ 0.5 且 margin=0.15 ≥ 0.15 → selected
        ({"off": 0.55, "low": 0.40, "medium": 0.05, "high": 0.0, "uncertain": 0.0}, "off", "low"),
    ]
    for probs, expected_effort, expected_conservative in cases:
        mock_api.push(200, _effort_body(probs))
        data = await _call("effort_route", {"query": "q", "levels": LEVELS})
        assert data["verdict"] == "selected"
        assert data["effort"] == expected_effort
        assert data["conservative_level"] == expected_conservative


async def test_effort_custom_safety_threshold(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _effort_body(_effort_probs()))
    patched_tool_client(make_client(), MODULE)

    data = await _call(
        "effort_route",
        {"query": "q", "levels": LEVELS, "safety_threshold": 0.6},
    )

    # CDF medium ≈ 0.70 ≥ 0.6 → conservative 回落到 medium
    assert data["conservative_level"] == "medium"


# ---------- effort_route：verdict 分支与畸形响应（§7.3-3/4/5/7） ----------


async def test_effort_uncertain_escape(mock_api, make_client, patched_tool_client):
    probs = _effort_probs(uncertain=0.9, off=0.05, low=0.02, medium=0.02, high=0.01)
    mock_api.push(200, _effort_body(probs))
    patched_tool_client(make_client(), MODULE)

    data = await _call("effort_route", {"query": "弄一下", "levels": LEVELS})

    assert data["verdict"] == "uncertain"
    assert data["effort"] is None
    assert data["action"] == "review"
    assert data["conservative_level"] is not None  # 答案有效时恒输出


async def test_effort_low_confidence_review(mock_api, make_client, patched_tool_client):
    probs = _effort_probs(off=0.4, low=0.35, medium=0.15, high=0.09)
    mock_api.push(200, _effort_body(probs))
    patched_tool_client(make_client(), MODULE)

    data = await _call("effort_route", {"query": "q", "levels": LEVELS})

    assert data["verdict"] == "review"
    assert data["effort"] is None


async def test_effort_invalid_response(mock_api, make_client, patched_tool_client):
    body = _effort_body(_effort_probs())
    del body["answers"]["effort"]
    mock_api.push(200, body)
    patched_tool_client(make_client(), MODULE)

    data = await _call("effort_route", {"query": "q", "levels": LEVELS})

    assert data["verdict"] == "invalid_response"
    assert data["effort"] is None
    assert data["conservative_level"] is None
    assert "error" in data


# ---------- effort_route：截断 fail-closed（§7.3-8） ----------


async def test_effort_truncation_never_auto_pass(mock_api, make_client, patched_tool_client):
    probs = _effort_probs(off=0.99, low=0.005, medium=0.003, high=0.001, uncertain=0.001)
    mock_api.push(200, _effort_body(probs))
    patched_tool_client(make_client(), MODULE)

    data = await _call("effort_route", {"query": "z" * 50, "levels": LEVELS, "query_max_chars": 5})

    assert data["truncated"] is True
    assert data["verdict"] == "review"  # selected 降级 review
    assert data["effort"] is None


# ---------- effort_route：参数校验不发 HTTP（§7.3-9） ----------


async def test_effort_validation_errors_no_http(mock_api, make_client, patched_tool_client):
    patched_tool_client(make_client(), MODULE)
    cases = [
        {"query": "q", "levels": {"uncertain": "x", "low": "y"}},  # 保留名
        {"query": "q", "levels": {"ONLY": "x", "low": "y"}},  # 非法字符
        {"query": "q", "levels": {"only": "x"}},  # < 2
        {"query": "q", "levels": {f"l{i}": "x" for i in range(255)}},  # > 254
        {"query": "q", "levels": LEVELS, "effort_threshold": 2.0},  # 阈值越界
        {"query": "q", "levels": LEVELS, "safety_threshold": -0.5},
    ]
    for args in cases:
        with pytest.raises(ClientToolError):
            await _call("effort_route", args)
    assert len(mock_api.requests) == 0


# ---------- effort_route：HTTP 错误序列（§7.3-10） ----------


async def test_effort_network_error_never_resent(mock_api, make_client, patched_tool_client):
    mock_api.push_error(httpx.ReadTimeout("timed out"))
    patched_tool_client(make_client(max_retries=5), MODULE)

    data = await _call("effort_route", {"query": "q", "levels": LEVELS})

    assert len(mock_api.requests) == 1
    assert data["verdict"] == "invalid_response"
    assert data["level_order"] == ["off", "low", "medium", "high"]


# ---------- 真实 API 测试（烧 credits，默认不跑） ----------


@pytest.mark.real
async def test_real_effort_route():
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "effort_route",
        {
            "query": "1+1等于几",
            "levels": {
                "low": "轻量推理：事实性直答、一两步即可完成的简单推断",
                "medium": "中等推理：需要多步推理、综合多个信息或排查常规问题",
                "high": "深度推理：复杂规划、架构设计、疑难问题排查等高错误代价任务",
            },
        },
    )

    assert data["verdict"] == "selected"
    assert data["effort"] == "low"
    assert data["level_order"] == ["low", "medium", "high"]
    assert data["usage"]["input_tokens"] > 0
