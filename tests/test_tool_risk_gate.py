"""tool_risk_gate 测试。

mock 部分覆盖详设 §7.3：正常路径真值表、阈值参数化、fail-closed 畸形响应、
截断行为、重试序列、不重试路径（401/402/网络歧义错误/超时）、token 预算预检。
real 部分通过 fastmcp in-process Client 打真实 /api/v1/agent/risk。
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError as ClientToolError

from jev_harness_tool.core.client import reset_shared_client
from jev_harness_tool.server import create_server

from conftest import FAKE_API_KEY

MODULE = "jev_harness_tool.tools.tool_risk_gate"

DANGEROUS_ARGS = "rm -rf ./dist && aws s3 sync ./build s3://prod --delete"
BASE_ARGS = {"goal": "清理构建目录", "tool": "bash", "arguments": DANGEROUS_ARGS}


def make_risk_response(
    action: str = "block",
    risk: float = 0.84,
    categories: list | None = None,
    confidence: float | None = 0.6,
    usage: dict | None = None,
) -> dict:
    body: dict = {
        "action": action,
        "risk": risk,
        "categories": ["destructive", "irreversible"] if categories is None else categories,
        "confidence": confidence,
    }
    if usage is None:
        usage = {"input_tokens": 505, "output_tokens": 126,
                 "cost_usd": 0.000213, "credits_remaining_usd": 4.99}
    body["usage"] = usage
    return body


def _unwrap(result):
    if result.data is not None:
        return result.data
    return json.loads(result.content[0].text)


async def _call(args: dict) -> dict:
    async with Client(create_server()) as c:
        result = await c.call_tool("tool_risk_gate", args)
    return _unwrap(result)


# ---------- 1. 正常路径 ----------

async def test_block_roundtrip(mock_api, make_client, patched_tool_client):
    mock_api.push(200, make_risk_response())
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({**BASE_ARGS, "context": "CI 部署步骤"})

    assert data["verdict"] == "block"
    assert data["jev_action"] == "block"
    assert data["risk"] == 0.84
    assert data["categories"] == ["destructive", "irreversible"]
    assert data["confidence"] == 0.6
    assert data["truncated"] is False
    assert data["error"] is None
    # 阈值回显（默认值填充）
    assert data["thresholds"] == {"risk_allow_max": 0.3, "risk_block_min": 0.7,
                                  "confidence_min": 0.4}
    # usage 原样透传
    assert data["usage"]["input_tokens"] == 505
    assert data["usage"]["cost_usd"] == 0.000213
    # 请求体四字段原样映射，路径正确
    assert len(mock_api.requests) == 1
    request = mock_api.requests[0]
    assert request.url.path == "/api/v1/agent/risk"
    assert request.headers["authorization"] == f"Bearer {FAKE_API_KEY}"
    sent = json.loads(request.content)
    assert sent == {"goal": "清理构建目录", "tool": "bash",
                    "arguments": DANGEROUS_ARGS, "context": "CI 部署步骤"}


# ---------- 2. §4.3 真值表（未截断、响应合法） ----------

@pytest.mark.parametrize(
    "action,risk,confidence,expected",
    [
        ("allow", 0.1, 0.9, "allow"),     # 低风险高置信 → 放行
        ("allow", 0.1, 0.3, "review"),    # confidence 升档
        ("allow", 0.1, None, "review"),   # confidence 缺失视为未知 → 升档
        ("allow", 0.5, 0.9, "review"),    # risk 进入中间带
        ("allow", 0.8, 0.9, "block"),     # risk ≥ risk_block_min 强制 block
        ("confirm", 0.8, 0.9, "block"),
        ("confirm", 0.1, 0.9, "review"),  # confirm 基线即 review
        ("block", 0.1, 0.9, "block"),     # jev block 不被本地阈值放松
    ],
)
async def test_verdict_truth_table(mock_api, make_client, patched_tool_client,
                                   action, risk, confidence, expected):
    body = make_risk_response(action=action, risk=risk, confidence=confidence)
    if confidence is None:
        body.pop("confidence")
    mock_api.push(200, body)
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(BASE_ARGS)

    assert data["verdict"] == expected
    assert data["jev_action"] == action


# ---------- 3. 阈值参数化 ----------

async def test_custom_thresholds_applied_and_echoed(mock_api, make_client, patched_tool_client):
    # 自定义 risk_allow_max=0.05：risk=0.1 不再满足 allow 条件
    mock_api.push(200, make_risk_response(action="allow", risk=0.1, confidence=0.9))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({**BASE_ARGS, "risk_allow_max": 0.05})

    assert data["verdict"] == "review"
    assert data["thresholds"]["risk_allow_max"] == 0.05


async def test_invalid_thresholds_rejected_without_request(mock_api, make_client,
                                                           patched_tool_client):
    patched_tool_client(make_client(), module=MODULE)

    with pytest.raises(ClientToolError):
        await _call({**BASE_ARGS, "risk_allow_max": 0.7, "risk_block_min": 0.7})

    assert len(mock_api.requests) == 0  # 参数错误不发请求


async def test_oversized_goal_rejected_without_request(mock_api, make_client,
                                                       patched_tool_client):
    patched_tool_client(make_client(), module=MODULE)

    with pytest.raises(ClientToolError):
        await _call({**BASE_ARGS, "goal": "x" * 4001})

    assert len(mock_api.requests) == 0


# ---------- 4. 畸形响应 fail-closed ----------

@pytest.mark.parametrize(
    "body",
    [
        {"risk": 0.1, "categories": [], "confidence": 0.9},                 # 缺 action
        {"action": "allow", "risk": 1.7, "categories": [], "confidence": 0.9},  # risk 越界
        {"action": "allow", "risk": 0.1, "categories": "oops", "confidence": 0.9},  # 类型错误
        {"action": "nuke", "risk": 0.1, "categories": [], "confidence": 0.9},  # 非法 action
    ],
)
async def test_malformed_response_is_invalid_response(mock_api, make_client,
                                                      patched_tool_client, body):
    mock_api.push(200, body)
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(BASE_ARGS)

    assert data["verdict"] == "invalid_response"
    assert data["jev_action"] is None
    assert data["error"]["code"] == "invalid_response"
    assert data["verdict"] != "allow"  # 绝不伪装成放行


async def test_non_json_response_is_invalid_response(mock_api, make_client,
                                                     patched_tool_client):
    mock_api.push(200, None)  # 顶层非 JSON 对象
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(BASE_ARGS)

    assert data["verdict"] == "invalid_response"


# ---------- 5. 截断行为 ----------

async def test_truncation_caps_verdict_at_review(mock_api, make_client, patched_tool_client):
    long_args = "cat " + "x" * 500
    mock_api.push(200, make_risk_response(action="allow", risk=0.1, confidence=0.9))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({**BASE_ARGS, "arguments": long_args, "arguments_max_chars": 100})

    assert data["truncated"] is True
    assert data["verdict"] == "review"  # 截断输入永不自动放行
    sent = json.loads(mock_api.requests[0].content)
    assert sent["arguments"] == long_args[:100]


# ---------- 6. 重试纪律 ----------

async def test_retry_502_502_then_success(mock_api, make_client, patched_tool_client):
    mock_api.push(502, {"error": "upstream"}).push(502, {"error": "upstream"}) \
        .push(200, make_risk_response())
    patched_tool_client(make_client(max_retries=3), module=MODULE)

    data = await _call(BASE_ARGS)

    assert data["verdict"] == "block"
    assert len(mock_api.requests) == 3


async def test_retry_exhausted_returns_upstream_unavailable(mock_api, make_client,
                                                            patched_tool_client):
    for _ in range(3):
        mock_api.push(502, {"error": "upstream"})
    patched_tool_client(make_client(max_retries=2), module=MODULE)

    data = await _call(BASE_ARGS)

    assert data["verdict"] is None
    assert data["error"]["code"] == "upstream_unavailable"
    assert len(mock_api.requests) == 3  # 1 initial + 2 retries


async def test_401_not_retried(mock_api, make_client, patched_tool_client):
    mock_api.push(401, {"error": "invalid key"})
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(BASE_ARGS)

    assert data["error"]["code"] == "auth_error"
    assert data["error"]["retryable"] is False
    assert len(mock_api.requests) == 1


async def test_402_insufficient_credits_not_retried(mock_api, make_client, patched_tool_client):
    mock_api.push(402, {"code": "insufficient_credits"})
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(BASE_ARGS)

    assert data["error"]["code"] == "insufficient_credits"
    assert len(mock_api.requests) == 1


async def test_ambiguous_network_error_never_resent(mock_api, make_client, patched_tool_client):
    mock_api.push_error(httpx.ConnectError("connection reset by peer"))
    patched_tool_client(make_client(max_retries=5), module=MODULE)

    data = await _call(BASE_ARGS)

    assert data["error"]["code"] == "network_error"
    assert data["error"]["retryable"] is False
    assert len(mock_api.requests) == 1  # 歧义网络错误绝不重发


async def test_timeout_never_resent(mock_api, make_client, patched_tool_client):
    mock_api.push_error(httpx.ReadTimeout("timed out"))
    patched_tool_client(make_client(max_retries=5), module=MODULE)

    data = await _call(BASE_ARGS)

    assert data["error"]["code"] == "timeout"
    assert len(mock_api.requests) == 1


# ---------- 7. token 预算预检 ----------

async def test_input_too_large_returns_error_without_request(mock_api, make_client,
                                                             patched_tool_client):
    patched_tool_client(make_client(), module=MODULE)
    huge = "1" * 100_000  # 数字 0.5 tok/字符 → ~50k est. > 30k 预算

    data = await _call({**BASE_ARGS, "arguments": huge, "arguments_max_chars": 200_000})

    assert data["error"]["code"] == "input_too_large"
    assert len(mock_api.requests) == 0


# ---------- 真实 API 测试（烧 credits，默认不跑） ----------

@pytest.fixture
def fresh_client():
    """真实测试前丢弃 core 共享单例（现由 get_shared_client 循环感知自动重建，
    本 fixture 仅为防御性重置）。"""
    reset_shared_client()


@pytest.mark.real
async def test_real_risk_gate_blocks_dangerous_command(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call({**BASE_ARGS, "context": "CI 部署步骤"})

    assert data["error"] is None
    assert data["verdict"] in ("block", "review")  # 危险命令绝不被自动放行
    assert data["risk"] is not None and data["risk"] > 0.5
    assert data["jev_action"] in ("confirm", "block")
    assert data["usage"]["input_tokens"] > 0
    assert data["thresholds"]["risk_allow_max"] == 0.3


@pytest.mark.real
async def test_real_risk_gate_allows_safe_read(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call({"goal": "了解项目结构", "tool": "read_file",
                        "arguments": "README.md"})

    assert data["error"] is None
    assert data["verdict"] == "allow"
    assert data["risk"] is not None and data["risk"] < 0.3
