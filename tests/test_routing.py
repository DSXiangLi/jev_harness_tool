"""route_decide + effort_route 工具测试。

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

from jev_harness_tool.core import tokens as tokens_mod
from jev_harness_tool.server import create_server
from jev_harness_tool.tools import routing

from conftest import make_decide_response

MODULE = "jev_harness_tool.tools.routing"

CANDIDATES = {
    "explore": "只读探索：检索、阅读、总结代码库或文档内容，不做任何修改",
    "code": "代码修改：编写、修改、重构代码并运行测试",
    "research": "外部调研：搜索网络资料、阅读外部文档并给出综述",
    "data": "数据分析：处理数据集、生成统计结果与图表",
}

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


def _route_body(probs: dict[str, float], *, complexity: dict | None = None) -> dict:
    answers = {"route": _choice_answer(probs)}
    if complexity is not None:
        answers["complexity"] = complexity
    return {
        "model": "jev-test",
        "answers": answers,
        "usage": {"input_tokens": 42, "cost_usd": 0.00001, "credits_remaining_usd": 4.99},
    }


def _route_probs(**overrides: float) -> dict[str, float]:
    probs = {"explore": 0.86, "code": 0.08, "research": 0.03, "data": 0.01, "none": 0.01, "uncertain": 0.01}
    probs.update(overrides)
    return probs


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


# ---------- route_decide：正常路径与请求纪律（§7.3-1/2） ----------


async def test_route_decide_routed(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _route_body(_route_probs(), complexity={"type": "score", "score": 2.0, "confidence": 0.8}))
    patched_tool_client(make_client(), MODULE)

    data = await _call("route_decide", {"query": "看看 src/auth 目录下登录逻辑是怎么实现的", "candidates": CANDIDATES})

    assert data["verdict"] == "routed"
    assert data["route"] == "explore"
    assert data["action"] == "allow"
    assert data["probability"] == pytest.approx(0.86)
    assert data["margin"] == pytest.approx(0.78)  # 0.86 − 0.08
    assert data["probabilities"]["none"] == pytest.approx(0.01)
    assert data["complexity"] == {"score": 2.0, "confidence": 0.8, "scale": 4}
    assert data["truncated"] is False
    assert data["thresholds"] == {"route_threshold": 0.5, "margin_threshold": 0.15}
    assert data["usage"]["input_tokens"] == 42
    assert data["stats"]["candidates_count"] == 4
    assert data["stats"]["include_complexity"] is True

    # 批量纪律：一次工具调用只产生 1 次 /decide 请求
    assert len(mock_api.requests) == 1
    sent = json.loads(mock_api.requests[0].content)
    # 逃生舱注入：criteria 含 none + uncertain，定义与详设逐字一致
    criteria = sent["questions"]["route"]["criteria"]
    assert set(criteria) == {*CANDIDATES, "none", "uncertain"}
    assert criteria["none"] == routing.ROUTE_ESCAPE_CRITERIA["none"]
    assert criteria["uncertain"] == routing.ROUTE_ESCAPE_CRITERIA["uncertain"]
    for name, desc in CANDIDATES.items():
        assert criteria[name] == desc
    # questions = route + complexity；state 结构正确
    assert set(sent["questions"]) == {"route", "complexity"}
    assert sent["questions"]["complexity"]["criteria"] == routing.COMPLEXITY_SCALE
    assert sent["state"]["query"] == "看看 src/auth 目录下登录逻辑是怎么实现的"
    assert sent["state"]["candidates"] == CANDIDATES
    assert sent["state"]["truncated"] is False


async def test_route_decide_without_complexity(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _route_body(_route_probs()))
    patched_tool_client(make_client(), MODULE)

    data = await _call(
        "route_decide",
        {"query": "修复登录接口 500 错误并补测试", "candidates": CANDIDATES, "include_complexity": False},
    )

    assert data["verdict"] == "routed"
    assert data["complexity"] is None
    sent = json.loads(mock_api.requests[0].content)
    assert set(sent["questions"]) == {"route"}  # include_complexity=false → 只有 1 个 question


async def test_route_decide_custom_thresholds_echoed(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _route_body(_route_probs()))
    patched_tool_client(make_client(), MODULE)

    data = await _call(
        "route_decide",
        {"query": "q", "candidates": CANDIDATES, "route_threshold": 0.9, "margin_threshold": 0.5},
    )

    assert data["verdict"] == "review"  # 0.86 < 0.9
    assert data["thresholds"] == {"route_threshold": 0.9, "margin_threshold": 0.5}


# ---------- route_decide：verdict 分支（§7.3-3/4/5） ----------


async def test_route_margin_demotes_to_review(mock_api, make_client, patched_tool_client):
    probs = {"explore": 0.5, "code": 0.4, "research": 0.05, "data": 0.03, "none": 0.01, "uncertain": 0.01}
    mock_api.push(200, _route_body(probs))
    patched_tool_client(make_client(), MODULE)

    data = await _call("route_decide", {"query": "调研竞品实现然后改进我们的版本", "candidates": CANDIDATES})

    assert data["verdict"] == "review"  # p=0.5 过线但 margin=0.1 < 0.15（原则 5）
    assert data["route"] is None
    assert data["action"] == "review"


async def test_route_low_confidence_review(mock_api, make_client, patched_tool_client):
    probs = _route_probs(explore=0.49, code=0.30)
    mock_api.push(200, _route_body(probs))
    patched_tool_client(make_client(), MODULE)

    data = await _call("route_decide", {"query": "q", "candidates": CANDIDATES})

    assert data["verdict"] == "review"
    assert data["route"] is None


async def test_route_threshold_boundary_exact_is_accept(mock_api, make_client, patched_tool_client):
    probs = {"explore": 0.5, "code": 0.3, "research": 0.1, "data": 0.05, "none": 0.03, "uncertain": 0.02}
    mock_api.push(200, _route_body(probs))
    patched_tool_client(make_client(), MODULE)

    data = await _call("route_decide", {"query": "q", "candidates": CANDIDATES})

    assert data["verdict"] == "routed"  # p 恰好等于阈值 → ≥ 语义，自动裁决
    assert data["route"] == "explore"


async def test_route_no_route_escape(mock_api, make_client, patched_tool_client):
    probs = _route_probs(none=0.8, explore=0.1, code=0.05, research=0.02, data=0.02, uncertain=0.01)
    mock_api.push(200, _route_body(probs))
    patched_tool_client(make_client(), MODULE)

    data = await _call("route_decide", {"query": "订一张明天去上海的机票", "candidates": CANDIDATES})

    assert data["verdict"] == "no_route"
    assert data["route"] is None
    assert data["action"] == "review"


async def test_route_uncertain_escape(mock_api, make_client, patched_tool_client):
    probs = _route_probs(uncertain=0.9, explore=0.05, code=0.02, research=0.01, data=0.01, none=0.01)
    mock_api.push(200, _route_body(probs))
    patched_tool_client(make_client(), MODULE)

    data = await _call("route_decide", {"query": "帮我处理一下那个事情", "candidates": CANDIDATES})

    assert data["verdict"] == "uncertain"
    assert data["route"] is None
    assert data["action"] == "review"


# ---------- route_decide：畸形响应与字段级隔离（§7.3-7） ----------


async def test_route_missing_answer_invalid_response(mock_api, make_client, patched_tool_client):
    body = _route_body(_route_probs(), complexity={"type": "score", "score": 2.0})
    del body["answers"]["route"]
    mock_api.push(200, body)
    patched_tool_client(make_client(), MODULE)

    data = await _call("route_decide", {"query": "q", "candidates": CANDIDATES})

    assert data["verdict"] == "invalid_response"
    assert data["route"] is None
    assert data["probability"] is None
    assert data["action"] == "review"
    assert "error" in data


async def test_route_choice_not_argmax_invalid(mock_api, make_client, patched_tool_client):
    answer = _choice_answer(_route_probs())
    answer["choice"] = "code"  # argmax 是 explore
    body = _route_body(_route_probs())
    body["answers"]["route"] = answer
    mock_api.push(200, body)
    patched_tool_client(make_client(), MODULE)

    data = await _call("route_decide", {"query": "q", "candidates": CANDIDATES})

    assert data["verdict"] == "invalid_response"


async def test_route_probability_out_of_range_invalid(mock_api, make_client, patched_tool_client):
    body = _route_body(_route_probs())
    body["answers"]["route"] = {"type": "choice", "choice": "explore", "probabilities": {"explore": 1.7}}
    mock_api.push(200, body)
    patched_tool_client(make_client(), MODULE)

    data = await _call("route_decide", {"query": "q", "candidates": CANDIDATES})

    assert data["verdict"] == "invalid_response"


async def test_route_complexity_malformed_isolated(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _route_body(_route_probs(), complexity={"type": "score"}))  # 缺 score 字段
    patched_tool_client(make_client(), MODULE)

    data = await _call("route_decide", {"query": "q", "candidates": CANDIDATES})

    assert data["verdict"] == "routed"  # complexity 畸形不炸路由 verdict
    assert data["complexity"] is None


# ---------- route_decide：截断 fail-closed（§7.3-8） ----------


async def test_route_truncation_never_auto_pass(mock_api, make_client, patched_tool_client):
    probs = _route_probs(explore=0.99, code=0.005, research=0.002, data=0.001, none=0.001, uncertain=0.001)
    mock_api.push(200, _route_body(probs))
    patched_tool_client(make_client(), MODULE)

    data = await _call(
        "route_decide",
        {"query": "x" * 100, "candidates": CANDIDATES, "query_max_chars": 10},
    )

    assert data["truncated"] is True
    assert data["verdict"] == "review"  # 即便 mock 返回 0.99 高置信也不自动通过
    assert data["route"] is None
    sent = json.loads(mock_api.requests[0].content)
    assert sent["state"]["query"] == "x" * 10  # 头部保留截断
    assert sent["state"]["truncated"] is True


async def test_route_description_truncation_marks_truncated(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _route_body(_route_probs()))
    patched_tool_client(make_client(), MODULE)
    candidates = {**CANDIDATES, "code": "y" * 300}

    data = await _call(
        "route_decide",
        {"query": "q", "candidates": candidates, "description_max_chars": 200},
    )

    assert data["truncated"] is True
    assert data["verdict"] == "review"
    sent = json.loads(mock_api.requests[0].content)
    assert len(sent["questions"]["route"]["criteria"]["code"]) == 200


# ---------- route_decide：参数校验不发 HTTP（§7.3-9） ----------


async def test_route_validation_errors_no_http(mock_api, make_client, patched_tool_client):
    patched_tool_client(make_client(), MODULE)
    cases = [
        {"query": "q", "candidates": {"none": "x", "code": "y"}},  # 保留名 none
        {"query": "q", "candidates": {"uncertain": "x", "code": "y"}},  # 保留名 uncertain
        {"query": "q", "candidates": {"Bad Name": "x", "code": "y"}},  # 非法字符
        {"query": "q", "candidates": {"only": "x"}},  # 数量越界（< 2）
        {"query": "q", "candidates": {f"c{i}": "x" for i in range(254)}},  # 数量越界（> 253）
        {"query": "q", "candidates": {"a": "x", "b": ""}},  # 空 description
        {"query": "  ", "candidates": CANDIDATES},  # 空 query
        {"query": "q", "candidates": CANDIDATES, "route_threshold": 1.5},  # 阈值越界
        {"query": "q", "candidates": CANDIDATES, "margin_threshold": -0.1},
    ]
    for args in cases:
        with pytest.raises(ClientToolError):
            await _call("route_decide", args)
    assert len(mock_api.requests) == 0  # 校验失败不发 HTTP 请求


# ---------- route_decide：HTTP 错误序列（§7.3-10） ----------


async def test_route_retry_on_502_then_success(mock_api, make_client, patched_tool_client):
    mock_api.push(502, {"error": "upstream"}).push(502, {"error": "upstream"})
    mock_api.push(200, _route_body(_route_probs()))
    patched_tool_client(make_client(max_retries=3), MODULE)

    data = await _call("route_decide", {"query": "q", "candidates": CANDIDATES})

    assert data["verdict"] == "routed"
    assert len(mock_api.requests) == 3


@pytest.mark.parametrize("status", [401, 402, 403])
async def test_route_auth_payment_errors_not_retried(mock_api, make_client, patched_tool_client, status):
    mock_api.push(status, {"error": "nope"})
    patched_tool_client(make_client(), MODULE)

    with pytest.raises(ClientToolError):
        await _call("route_decide", {"query": "q", "candidates": CANDIDATES})
    assert len(mock_api.requests) == 1  # 401/402/403 不重试


async def test_route_network_error_never_resent(mock_api, make_client, patched_tool_client):
    mock_api.push_error(httpx.ConnectError("connection reset by peer"))
    patched_tool_client(make_client(max_retries=5), MODULE)

    data = await _call("route_decide", {"query": "q", "candidates": CANDIDATES})

    # 歧义网络错误：绝不重发，fail-closed 为 invalid_response（不抛工具错误）
    assert len(mock_api.requests) == 1
    assert data["verdict"] == "invalid_response"
    assert data["action"] == "review"
    assert data["usage"] is None
    assert "JevNetworkError" in data["error"]


async def test_route_input_too_large_no_http(mock_api, make_client, patched_tool_client, monkeypatch):
    monkeypatch.setattr(tokens_mod, "TOTAL_TOKEN_BUDGET", 50)
    patched_tool_client(make_client(), MODULE)

    with pytest.raises(ClientToolError, match="input_too_large"):
        await _call("route_decide", {"query": "q", "candidates": CANDIDATES})
    assert len(mock_api.requests) == 0


# ---------- effort_route：正常路径与保守选档（§7.3-6） ----------


async def test_effort_selected_with_conservative_shift(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _effort_body(_effort_probs()))
    patched_tool_client(make_client(), MODULE)

    data = await _call("effort_route", {"query": "设计一个支持断点续传的分布式任务调度方案", "levels": LEVELS})

    assert data["verdict"] == "selected"
    assert data["effort"] == "medium"
    assert data["action"] == "allow"
    assert data["probability"] == pytest.approx(0.62)
    assert data["margin"] == pytest.approx(0.32)
    # 详设 §4.4 示例 3：剔除 uncertain 重归一化后 CDF ≈ .02/.07/.70/1.00 → conservative = high
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
    """详设 §4.4 示例 1/2：重归一化与 CDF 计算正确。"""
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


# ---------- effort_route：verdict 分支与畸形响应 ----------


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


async def test_effort_truncation_never_auto_pass(mock_api, make_client, patched_tool_client):
    probs = _effort_probs(off=0.99, low=0.005, medium=0.003, high=0.001, uncertain=0.001)
    mock_api.push(200, _effort_body(probs))
    patched_tool_client(make_client(), MODULE)

    data = await _call("effort_route", {"query": "z" * 50, "levels": LEVELS, "query_max_chars": 5})

    assert data["truncated"] is True
    assert data["verdict"] == "review"  # selected 降级 review
    assert data["effort"] is None


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


async def test_effort_network_error_never_resent(mock_api, make_client, patched_tool_client):
    mock_api.push_error(httpx.ReadTimeout("timed out"))
    patched_tool_client(make_client(max_retries=5), MODULE)

    data = await _call("effort_route", {"query": "q", "levels": LEVELS})

    assert len(mock_api.requests) == 1
    assert data["verdict"] == "invalid_response"
    assert data["level_order"] == ["off", "low", "medium", "high"]


# ---------- 组合调用（§7.3-11）：route + effort 一次 /decide ----------


async def test_combined_route_effort_single_call(mock_api, make_client, patched_tool_client):
    questions = {**routing.route_questions(CANDIDATES), **routing.effort_questions(LEVELS)}
    assert set(questions) == {"route", "complexity", "effort"}  # 命名空间无冲突
    mock_api.push(200, make_decide_response(questions))
    patched_tool_client(make_client())  # 组合调用走 jev_decide 原语（默认 module）

    state = {"query": "q", "candidates": CANDIDATES, "levels": LEVELS, "truncated": False}
    data = await _call("jev_decide", {"state": state, "questions": questions})

    assert len(mock_api.requests) == 1  # state 只发一份，一次 HTTP 往返
    sent = json.loads(mock_api.requests[0].content)
    assert sent["state"] == state
    assert set(data["answers"]) == {"route", "complexity", "effort"}


# ---------- 真实 API 测试（烧 credits，默认不跑） ----------


@pytest.mark.real
async def test_real_route_decide():
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "route_decide",
        {
            "query": "帮我查一下这个函数的调用链",
            "candidates": {
                "explore": "只读代码探索：检索、阅读、总结代码库内容，不做任何修改",
                "general": "通用执行：处理代码修改、命令执行等需要实际操作的请求",
            },
        },
    )

    assert data["verdict"] == "routed"
    assert data["route"] == "explore"
    assert data["action"] == "allow"
    assert data["probability"] >= data["thresholds"]["route_threshold"]
    assert data["usage"]["input_tokens"] > 0


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
