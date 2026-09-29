"""context_filter 工具测试。

① mock 测试（默认，不触网）：MockJevAPI + patched_tool_client 注入，覆盖详设 §7.3——
   双路径选择（context/filter vs /decide）、单条 verdict 真值表（§4.3）、批量 verdict
   双判据与冗余覆盖（§4.2）、invalid_response 隔离、wire 位置 key 隔离、截断封顶、
   token 分批与 usage 合计、批次失败隔离、重试纪律（502 重试 / 401·402·网络歧义·超时
   不重发）、参数校验不发请求。
② 真实 API 测试（@pytest.mark.real）：fastmcp in-process Client 真实调用两条路径。
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError as ClientToolError

from jev_harness_tool.core.client import JevClient, reset_shared_client
from jev_harness_tool.core import tokens as tokens_mod
from jev_harness_tool.server import create_server

from conftest import MockJevAPI, make_settings

MODULE = "jev_harness_tool.tools.context_filter"

TASK = "修复支付 webhook 的测试失败"
ITEM = "Tool call: read_file('README.md') -> 4000 tokens 项目概览，来自 30 步之前"

ITEMS_3 = [
    {"id": "msg_rel", "content": "pytest 输出：test_webhook_timeout FAILED，payments/webhook.py:87，尚未修复"},
    {"id": "msg_irr", "content": ITEM},
    {"id": "msg_mid", "content": "早期方案讨论：webhook 重试是否上 Redis 队列，结论是先修测试"},
]


def _unwrap(result):
    if result.data is not None:
        return result.data
    return json.loads(result.content[0].text)


def _usage(input_tokens: int = 100) -> dict:
    return {"input_tokens": input_tokens, "cost_usd": 0.00001, "credits_remaining_usd": 4.99}


def _filter_response(
    action: str = "drop",
    relevance: str = "minor",
    redundant: bool = True,
    confidence: float | None = 0.53,
    input_tokens: int = 120,
) -> dict:
    body: dict = {"action": action, "relevance": relevance, "redundant": redundant}
    if confidence is not None:
        body["confidence"] = confidence
    body["usage"] = _usage(input_tokens)
    return body


def _batch_body(specs: dict[str, tuple[dict[str, float], float | None]], input_tokens: int = 100) -> dict:
    """specs: 位置 key -> (choice 概率分布, 冗余 noul 概率或 None)。"""
    answers: dict = {}
    for key, (probs, r) in specs.items():
        winner = max(probs, key=lambda k: probs[k])
        answers[f"{key}_action"] = {
            "type": "choice",
            "choice": winner,
            "confidence": probs[winner],
            "probabilities": probs,
        }
        if r is not None:
            answers[f"{key}_redundant"] = {"type": "noul", "noul": r}
    return {"model": "jev-test", "answers": answers, "usage": _usage(input_tokens)}


async def _call(args: dict) -> dict:
    async with Client(create_server()) as c:
        result = await c.call_tool("context_filter", args)
    return _unwrap(result)


async def _no_sleep(_seconds: float) -> None:
    return None


class RoutedMockAPI(MockJevAPI):
    """按请求内容路由响应的 mock（多批并发时请求顺序不确定，不能靠脚本队列）。"""

    def __init__(self, handler):
        super().__init__()
        self._route = handler

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        status, payload = self._route(body)
        return httpx.Response(status, json=payload, request=request)


def _routed_client(route, **overrides):
    routed = RoutedMockAPI(route)
    client = JevClient(make_settings(**overrides), transport=routed.transport, sleep=_no_sleep)
    return routed, client


# ---------- 1. 单条路径：正常路径与请求构造 ----------


async def test_single_roundtrip_hits_filter_endpoint(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _filter_response())
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({"task": TASK, "item": ITEM})

    # redundant=true 且 relevance=minor ≤ redundant_drop_max_relevance → drop（§4.3）
    assert data["verdict"] == "drop"
    assert data["jev_action"] == "drop"
    assert data["relevance"] == "minor"
    assert data["redundant"] is True
    assert data["confidence"] == 0.53
    assert data["truncated"] is False
    assert data["error"] is None
    assert data["thresholds"] == {
        "keep_min_relevance": "moderate",
        "redundant_drop_max_relevance": "minor",
        "confidence_min": 0.4,
    }
    assert data["usage"]["input_tokens"] == 120

    assert len(mock_api.requests) == 1
    request = mock_api.requests[0]
    assert request.url.path == "/api/v1/context/filter"
    sent = json.loads(request.content)
    assert sent == {"task": TASK, "item": ITEM}  # 字段边界隔离，不拼接指令性文本


# ---------- 2. 单条路径：§4.3 verdict 真值表 ----------


@pytest.mark.parametrize(
    "action,relevance,redundant,confidence,expected",
    [
        ("keep", "high", False, 0.9, "keep"),
        ("keep", "moderate", False, 0.9, "keep"),       # moderate ≥ keep_min_relevance
        ("keep", "minor", False, 0.9, "truncate"),      # keep 门槛：relevance 不足
        ("keep", "none", False, 0.9, "truncate"),
        ("keep", "high", True, 0.9, "keep"),            # 冗余但相关性高，不触发 drop
        ("keep", "minor", True, 0.9, "drop"),           # 冗余 + 低相关 → drop
        ("keep", "moderate", True, 0.9, "keep"),        # 冗余但 moderate > minor
        ("truncate", "moderate", False, 0.9, "truncate"),
        ("truncate", "minor", True, 0.9, "drop"),       # 冗余规则覆盖任意 action
        ("drop", "none", False, 0.9, "drop"),
        ("drop", "minor", False, 0.9, "drop"),          # minor < moderate，无矛盾
        ("drop", "moderate", False, 0.9, "review"),     # jev 动作与 relevance 矛盾
        ("drop", "high", False, 0.9, "review"),
        ("drop", "high", True, 0.9, "drop"),            # 冗余时不做矛盾检查
        ("keep", "high", False, 0.3, "truncate"),       # confidence 保守化
        ("keep", "high", False, None, "truncate"),      # confidence 缺失视为未知
        ("truncate", "moderate", False, 0.3, "truncate"),
        ("drop", "none", False, 0.3, "review"),         # drop 保守化为 review
    ],
)
async def test_single_verdict_truth_table(mock_api, make_client, patched_tool_client,
                                          action, relevance, redundant, confidence, expected):
    mock_api.push(200, _filter_response(action=action, relevance=relevance,
                                        redundant=redundant, confidence=confidence))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({"task": TASK, "item": ITEM})

    assert data["verdict"] == expected
    assert data["jev_action"] == action


async def test_single_custom_thresholds_applied_and_echoed(mock_api, make_client,
                                                           patched_tool_client):
    # keep_min_relevance=high：moderate 不再够 keep；redundant_drop_max_relevance=moderate：
    # 冗余 + moderate 也 drop
    mock_api.push(200, _filter_response(action="keep", relevance="moderate",
                                        redundant=True, confidence=0.9))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({"task": TASK, "item": ITEM, "keep_min_relevance": "high",
                        "redundant_drop_max_relevance": "moderate"})

    assert data["verdict"] == "drop"
    assert data["thresholds"]["keep_min_relevance"] == "high"
    assert data["thresholds"]["redundant_drop_max_relevance"] == "moderate"


# ---------- 3. 单条路径：截断封顶 ----------


async def test_single_truncation_caps_keep(mock_api, make_client, patched_tool_client):
    long_item = "x" * 500
    mock_api.push(200, _filter_response(action="keep", relevance="high",
                                        redundant=False, confidence=0.9))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({"task": TASK, "item": long_item, "item_max_chars": 50})

    assert data["truncated"] is True
    assert data["verdict"] == "truncate"  # 截断条目永不自动 keep（原则 12）
    sent = json.loads(mock_api.requests[0].content)
    assert sent["item"] == long_item[:50]


# ---------- 4. 单条路径：畸形响应 fail-closed ----------


@pytest.mark.parametrize(
    "body",
    [
        {"relevance": "minor", "redundant": True, "confidence": 0.9},          # 缺 action
        {"action": "nuke", "relevance": "minor", "redundant": True},           # 非法 action
        {"action": "keep", "relevance": "extreme", "redundant": True},         # 非法 relevance
        {"action": "keep", "relevance": "minor", "redundant": "yes"},          # redundant 非布尔
        {"action": "keep", "relevance": "minor", "redundant": True, "confidence": 1.5},  # 越界
    ],
)
async def test_single_malformed_response_is_invalid_response(mock_api, make_client,
                                                             patched_tool_client, body):
    mock_api.push(200, body)
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({"task": TASK, "item": ITEM})

    assert data["verdict"] == "invalid_response"
    assert data["error"]["code"] == "invalid_response"
    assert data["jev_action"] is None  # 绝不伪装成任何动作


async def test_single_protocol_error_is_invalid_response(mock_api, make_client,
                                                         patched_tool_client):
    mock_api.push(200, None)  # 顶层非 JSON 对象 → JevProtocolError
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({"task": TASK, "item": ITEM})

    assert data["verdict"] == "invalid_response"
    assert data["error"]["code"] == "invalid_response"


# ---------- 5. 单条路径：重试纪律（原则 10） ----------


async def test_single_retry_502_502_then_success(mock_api, make_client, patched_tool_client):
    mock_api.push(502, {"error": "upstream"}).push(502, {"error": "upstream"}) \
        .push(200, _filter_response())
    patched_tool_client(make_client(max_retries=3), module=MODULE)

    data = await _call({"task": TASK, "item": ITEM})

    assert data["verdict"] == "drop"
    assert len(mock_api.requests) == 3


async def test_single_retry_exhausted_returns_upstream_unavailable(mock_api, make_client,
                                                                   patched_tool_client):
    for _ in range(3):
        mock_api.push(502, {"error": "upstream"})
    patched_tool_client(make_client(max_retries=2), module=MODULE)

    data = await _call({"task": TASK, "item": ITEM})

    assert data["verdict"] is None
    assert data["error"]["code"] == "upstream_unavailable"
    assert len(mock_api.requests) == 3  # 1 initial + 2 retries


async def test_single_401_not_retried(mock_api, make_client, patched_tool_client):
    mock_api.push(401, {"error": "invalid key"})
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({"task": TASK, "item": ITEM})

    assert data["error"]["code"] == "auth_error"
    assert len(mock_api.requests) == 1


async def test_single_402_insufficient_credits_not_retried(mock_api, make_client,
                                                           patched_tool_client):
    mock_api.push(402, {"code": "insufficient_credits"})
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({"task": TASK, "item": ITEM})

    assert data["error"]["code"] == "insufficient_credits"
    assert len(mock_api.requests) == 1


async def test_single_ambiguous_network_error_never_resent(mock_api, make_client,
                                                           patched_tool_client):
    mock_api.push_error(httpx.ConnectError("connection reset by peer"))
    patched_tool_client(make_client(max_retries=5), module=MODULE)

    data = await _call({"task": TASK, "item": ITEM})

    assert data["error"]["code"] == "network_error"
    assert data["error"]["retryable"] is False
    assert len(mock_api.requests) == 1  # 歧义网络错误绝不重发


async def test_single_timeout_never_resent(mock_api, make_client, patched_tool_client):
    mock_api.push_error(httpx.ReadTimeout("timed out"))
    patched_tool_client(make_client(max_retries=5), module=MODULE)

    data = await _call({"task": TASK, "item": ITEM})

    assert data["error"]["code"] == "timeout"
    assert len(mock_api.requests) == 1


async def test_single_input_too_large_returns_error_without_request(mock_api, make_client,
                                                                    patched_tool_client):
    patched_tool_client(make_client(), module=MODULE)
    huge = "1" * 100_000  # 数字 0.5 tok/字符 → ~50k est. > 30k 预算

    data = await _call({"task": TASK, "item": huge, "item_max_chars": 200_000})

    assert data["error"]["code"] == "input_too_large"
    assert len(mock_api.requests) == 0


# ---------- 6. 路径选择与参数校验（不发请求） ----------


async def test_both_item_and_items_rejected_without_request(mock_api, make_client,
                                                            patched_tool_client):
    patched_tool_client(make_client(), module=MODULE)

    with pytest.raises(ClientToolError):
        await _call({"task": TASK, "item": ITEM, "items": ITEMS_3})

    assert len(mock_api.requests) == 0


async def test_neither_item_nor_items_rejected_without_request(mock_api, make_client,
                                                               patched_tool_client):
    patched_tool_client(make_client(), module=MODULE)

    with pytest.raises(ClientToolError):
        await _call({"task": TASK})

    assert len(mock_api.requests) == 0


async def test_invalid_params_rejected_without_request(mock_api, make_client,
                                                       patched_tool_client):
    patched_tool_client(make_client(), module=MODULE)

    with pytest.raises(ClientToolError, match="task"):
        await _call({"task": "x" * 4001, "item": ITEM})
    with pytest.raises(ClientToolError, match="keep_min_relevance"):
        await _call({"task": TASK, "item": ITEM, "keep_min_relevance": "extreme"})
    with pytest.raises(ClientToolError, match="confidence_min"):
        await _call({"task": TASK, "item": ITEM, "confidence_min": 1.5})
    with pytest.raises(ClientToolError, match="action_prob_min"):
        await _call({"task": TASK, "items": ITEMS_3, "action_prob_min": -0.1})
    with pytest.raises(ClientToolError, match="500"):
        await _call({"task": TASK, "items": [{"id": i, "content": "c"} for i in range(501)]})
    with pytest.raises(ClientToolError, match="items"):
        await _call({"task": TASK, "items": []})
    with pytest.raises(ClientToolError, match="content"):
        await _call({"task": TASK, "items": [{"id": "a", "content": ""}]})

    assert len(mock_api.requests) == 0


# ---------- 7. 批量路径：正常路径与 wire 构造 ----------


async def test_batch_happy_path(mock_api, make_client, patched_tool_client):
    specs = {
        "i0": ({"keep": 0.9, "truncate": 0.08, "drop": 0.02}, 0.04),
        "i1": ({"keep": 0.05, "truncate": 0.15, "drop": 0.8}, 0.87),
        "i2": ({"keep": 0.1, "truncate": 0.7, "drop": 0.2}, 0.2),
    }
    mock_api.push(200, _batch_body(specs, input_tokens=4120))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({"task": TASK, "items": ITEMS_3})

    assert [r["id"] for r in data["results"]] == ["msg_rel", "msg_irr", "msg_mid"]
    assert [r["verdict"] for r in data["results"]] == ["keep", "drop", "truncate"]
    rel, irr, _mid = data["results"]
    assert rel["probabilities"] == {"keep": 0.9, "truncate": 0.08, "drop": 0.02}
    assert rel["margin"] == pytest.approx(0.82)
    assert rel["redundant"] == pytest.approx(0.04)
    assert rel["truncated"] is False
    assert irr["redundant"] == pytest.approx(0.87)
    assert data["summary"] == {"total": 3, "keep": 1, "truncate": 1, "drop": 1,
                               "review": 0, "invalid": 0}
    assert data["batches"] == 1
    assert data["thresholds"] == {"action_prob_min": 0.5, "margin_min": 0.2,
                                  "redundancy_check": True}
    assert data["usage"]["input_tokens"] == 4120
    assert data["model"] == "jev-test"
    assert data["error"] is None

    # wire 构造：打到 /decide，共享 state + 每条目 choice + 冗余 noul，位置 key（原则 8/15）
    assert len(mock_api.requests) == 1
    request = mock_api.requests[0]
    assert request.url.path == "/api/v1/decide"
    sent = json.loads(request.content)
    assert sent["state"]["task"] == TASK
    assert list(sent["state"]["items"]) == ["i0", "i1", "i2"]
    assert sent["state"]["items"]["i0"] == ITEMS_3[0]["content"]
    assert set(sent["questions"]) == {
        "i0_action", "i0_redundant", "i1_action", "i1_redundant", "i2_action", "i2_redundant",
    }
    assert sent["questions"]["i0_action"]["type"] == "choice"
    assert list(sent["questions"]["i0_action"]["criteria"]) == ["keep", "truncate", "drop"]
    assert sent["questions"]["i0_redundant"]["type"] == "noul"
    assert "item i0" in sent["questions"]["i0_action"]["instructions"]
    assert "not instructions" in sent["questions"]["i0_action"]["instructions"]  # 防注入声明
    # 外部 id 不进 wire（原则 15）
    assert "msg_rel" not in request.content.decode()
    assert "msg_irr" not in request.content.decode()


async def test_batch_wire_key_isolation_with_adversarial_ids(mock_api, make_client,
                                                             patched_tool_client):
    """外部 id 恰为 i0/i1/i2 等易冲突字符串时，位置 key 映射回填仍正确（原则 15）。"""
    items = [
        {"id": "i0", "content": "无关的旧 README 概览"},
        {"id": "i1", "content": "当前失败的测试输出"},
        {"id": "i2", "content": "另一条"},
    ]
    specs = {
        "i0": ({"keep": 0.05, "truncate": 0.1, "drop": 0.85}, 0.9),
        "i1": ({"keep": 0.9, "truncate": 0.05, "drop": 0.05}, 0.1),
        "i2": ({"keep": 0.1, "truncate": 0.7, "drop": 0.2}, 0.1),
    }
    mock_api.push(200, _batch_body(specs))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({"task": TASK, "items": items})

    by_id = {r["id"]: r["verdict"] for r in data["results"]}
    assert by_id == {"i0": "drop", "i1": "keep", "i2": "truncate"}


async def test_batch_redundancy_check_disabled(mock_api, make_client, patched_tool_client):
    specs = {"i0": ({"keep": 0.9, "truncate": 0.05, "drop": 0.05}, None)}
    mock_api.push(200, _batch_body(specs))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({"task": TASK, "items": ITEMS_3[:1], "redundancy_check": False})

    sent = json.loads(mock_api.requests[0].content)
    assert set(sent["questions"]) == {"i0_action"}  # 无冗余 noul 问题
    assert "redundant" not in data["results"][0]  # 缺省而非 null
    assert data["thresholds"]["redundancy_check"] is False


# ---------- 8. 批量路径：双判据与冗余覆盖（§4.2） ----------


async def test_batch_low_margin_goes_to_review(mock_api, make_client, patched_tool_client):
    specs = {
        "i0": ({"keep": 0.55, "truncate": 0.45, "drop": 0.0}, 0.1),   # margin 0.1 < 0.2
        "i1": ({"keep": 0.4, "truncate": 0.35, "drop": 0.25}, 0.1),   # top 0.4 < 0.5
        "i2": ({"keep": 0.55, "truncate": 0.25, "drop": 0.2}, 0.1),   # margin 0.3 ≥ 0.2 → 裁决
    }
    mock_api.push(200, _batch_body(specs))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({"task": TASK, "items": ITEMS_3})

    assert [r["verdict"] for r in data["results"]] == ["review", "review", "keep"]
    assert data["summary"]["review"] == 2


async def test_batch_custom_thresholds(mock_api, make_client, patched_tool_client):
    specs = {"i0": ({"keep": 0.55, "truncate": 0.45, "drop": 0.0}, 0.1)}
    mock_api.push(200, _batch_body(specs))
    patched_tool_client(make_client(), module=MODULE)

    # margin_min 降到 0.05：margin 0.1 通过 → keep
    data = await _call({"task": TASK, "items": ITEMS_3[:1], "margin_min": 0.05})

    assert data["results"][0]["verdict"] == "keep"
    assert data["thresholds"]["margin_min"] == 0.05


async def test_batch_redundant_override(mock_api, make_client, patched_tool_client):
    specs = {
        "i0": ({"keep": 0.9, "truncate": 0.05, "drop": 0.05}, 0.9),     # r≥0.8 + keep → truncate
        "i1": ({"keep": 0.1, "truncate": 0.8, "drop": 0.1}, 0.85),      # r≥0.8 + truncate → drop
        "i2": ({"keep": 0.9, "truncate": 0.05, "drop": 0.05}, 0.3),     # r 不足 → 不覆盖
    }
    mock_api.push(200, _batch_body(specs))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({"task": TASK, "items": ITEMS_3})

    assert [r["verdict"] for r in data["results"]] == ["truncate", "drop", "keep"]


async def test_batch_invalid_redundant_answer_disables_override(mock_api, make_client,
                                                                patched_tool_client):
    """冗余答案畸形 → redundant=null 且不触发覆盖（fail-closed：未知不降级），条目本身仍有效。"""
    body = _batch_body({"i0": ({"keep": 0.9, "truncate": 0.05, "drop": 0.05}, 0.9)})
    body["answers"]["i0_redundant"] = {"type": "noul", "noul": 1.5}  # 概率越界
    mock_api.push(200, body)
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({"task": TASK, "items": ITEMS_3[:1]})

    entry = data["results"][0]
    assert entry["verdict"] == "keep"  # 覆盖未触发
    assert entry["redundant"] is None
    assert data["summary"]["invalid"] == 0


async def test_batch_truncated_item_capped_at_truncate(mock_api, make_client,
                                                       patched_tool_client):
    items = [{"id": "long", "content": "y" * 500}, *ITEMS_3[1:]]
    specs = {
        "i0": ({"keep": 0.9, "truncate": 0.05, "drop": 0.05}, 0.1),
        "i1": ({"keep": 0.05, "truncate": 0.15, "drop": 0.8}, 0.87),
        "i2": ({"keep": 0.1, "truncate": 0.7, "drop": 0.2}, 0.2),
    }
    mock_api.push(200, _batch_body(specs))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({"task": TASK, "items": items, "item_max_chars": 50})

    entry = data["results"][0]
    assert entry["truncated"] is True
    assert entry["verdict"] == "truncate"  # 截断条目 verdict 封顶 truncate（原则 12）
    sent = json.loads(mock_api.requests[0].content)
    assert sent["state"]["items"]["i0"] == "y" * 50  # 请求中已头部截断


# ---------- 9. 批量路径：invalid_response 隔离（原则 13） ----------


async def test_batch_partial_invalid_isolated(mock_api, make_client, patched_tool_client):
    body = _batch_body({
        "i0": ({"keep": 0.9, "truncate": 0.05, "drop": 0.05}, 0.1),
        "i2": ({"keep": 0.05, "truncate": 0.15, "drop": 0.8}, 0.9),
    })
    # i1 的 choice 非 argmax → 畸形
    body["answers"]["i1_action"] = {
        "type": "choice", "choice": "keep",
        "probabilities": {"keep": 0.1, "truncate": 0.8, "drop": 0.1},
    }
    body["answers"]["i1_redundant"] = {"type": "noul", "noul": 0.1}
    mock_api.push(200, body)
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({"task": TASK, "items": ITEMS_3})

    # 单个坏答案只影响该条，不炸掉整个 batch
    assert [r["verdict"] for r in data["results"]] == ["keep", "invalid_response", "drop"]
    assert data["results"][1]["id"] == "msg_irr"
    assert "reason" in data["results"][1]
    assert data["summary"]["invalid"] == 1


async def test_batch_missing_answer_is_invalid(mock_api, make_client, patched_tool_client):
    body = _batch_body({"i0": ({"keep": 0.9, "truncate": 0.05, "drop": 0.05}, 0.1)})
    mock_api.push(200, body)  # i1/i2 答案整体缺失
    patched_tool_client(make_client(), module=MODULE)

    data = await _call({"task": TASK, "items": ITEMS_3})

    assert data["results"][0]["verdict"] == "keep"
    assert [r["verdict"] for r in data["results"][1:]] == ["invalid_response"] * 2
    assert data["results"][1]["reason"] == "missing answer"
    assert data["summary"]["invalid"] == 2


# ---------- 10. 批量路径：token 分批与批次失败隔离 ----------


async def test_batch_splitting_merge_and_usage_sum(patched_tool_client, monkeypatch):
    monkeypatch.setattr(tokens_mod, "TOTAL_TOKEN_BUDGET", 800)  # 强制每条目一批
    items = [{"id": f"it{i}", "content": "x" * 1200} for i in range(3)]

    def route(body):
        answers = {}
        for key in body["state"]["items"]:
            answers[f"{key}_action"] = {
                "type": "choice", "choice": "drop",
                "probabilities": {"keep": 0.05, "truncate": 0.1, "drop": 0.85},
            }
            answers[f"{key}_redundant"] = {"type": "noul", "noul": 0.9}
        return 200, {"model": "jev-test", "answers": answers, "usage": _usage(10)}

    routed, client = _routed_client(route)
    patched_tool_client(client, module=MODULE)

    data = await _call({"task": TASK, "items": items})

    assert data["batches"] == 3
    assert len(routed.requests) == 3
    assert [r["verdict"] for r in data["results"]] == ["drop"] * 3
    assert [r["id"] for r in data["results"]] == ["it0", "it1", "it2"]  # 顺序保持
    # 位置 key 全局连续，不随分批重置；state 随每批重发且含 task
    keys = sorted(k for r in routed.requests for k in json.loads(r.content)["state"]["items"])
    assert keys == ["i0", "i1", "i2"]
    assert all(json.loads(r.content)["state"]["task"] == TASK for r in routed.requests)
    # usage 各批合计，credits 取最后一批
    assert data["usage"]["input_tokens"] == 30


async def test_batch_partial_batch_failure_isolated(patched_tool_client, monkeypatch):
    monkeypatch.setattr(tokens_mod, "TOTAL_TOKEN_BUDGET", 800)
    items = [{"id": f"it{i}", "content": "x" * 1200} for i in range(3)]

    def route(body):
        if "i0" in body["state"]["items"]:
            return 502, {"error": "upstream"}
        answers = {}
        for key in body["state"]["items"]:
            answers[f"{key}_action"] = {
                "type": "choice", "choice": "keep",
                "probabilities": {"keep": 0.9, "truncate": 0.05, "drop": 0.05},
            }
            answers[f"{key}_redundant"] = {"type": "noul", "noul": 0.1}
        return 200, {"model": "jev-test", "answers": answers, "usage": _usage(10)}

    routed, client = _routed_client(route, max_retries=0)
    patched_tool_client(client, module=MODULE)

    data = await _call({"task": TASK, "items": items})

    # i0 所在批失败 → invalid_response；已成功批次照常返回（详设 §4.4/§5.3）
    assert data["results"][0]["verdict"] == "invalid_response"
    assert data["results"][0]["reason"] == "upstream_error"
    assert [r["verdict"] for r in data["results"][1:]] == ["keep", "keep"]
    assert data["summary"]["invalid"] == 1
    assert data["usage"]["input_tokens"] == 20  # 仅成功批次计入


async def test_batch_401_is_fatal_tool_error(mock_api, make_client, patched_tool_client):
    mock_api.push(401, {"error": "invalid key"})
    patched_tool_client(make_client(), module=MODULE)

    with pytest.raises(ClientToolError, match="JevAuthError"):
        await _call({"task": TASK, "items": ITEMS_3})
    assert len(mock_api.requests) == 1  # 零重试


async def test_batch_network_error_marks_all_invalid_without_resend(mock_api, make_client,
                                                                    patched_tool_client):
    mock_api.push_error(httpx.ConnectError("connection reset by peer"))
    patched_tool_client(make_client(max_retries=5), module=MODULE)

    data = await _call({"task": TASK, "items": ITEMS_3})

    assert len(mock_api.requests) == 1  # 歧义网络错误绝不重发
    assert all(r["verdict"] == "invalid_response" for r in data["results"])
    assert all(r["reason"] == "network_ambiguous" for r in data["results"])
    assert data["summary"]["invalid"] == 3


# ---------- 真实 API 测试（烧 credits，默认不跑） ----------


@pytest.fixture
def fresh_client():
    """真实测试前丢弃 core 共享单例（get_shared_client 循环感知会重建，防御性重置）。"""
    reset_shared_client()


@pytest.mark.real
async def test_real_single_stale_readme_dropped(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call({"task": TASK, "item": ITEM})

    assert data["error"] is None
    # 30 步前的 README 概览与支付 webhook 调试无关 → 不应保留
    assert data["verdict"] in ("drop", "truncate")
    # 语义信号：要么被判定冗余，要么相关性低
    assert data["redundant"] is True or data["relevance"] in ("none", "minor")
    assert data["jev_action"] in ("keep", "truncate", "drop")
    assert data["usage"]["input_tokens"] > 0


@pytest.mark.real
async def test_real_batch_three_items(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call({"task": TASK, "items": ITEMS_3})

    assert data["error"] is None
    assert data["summary"]["invalid"] == 0
    assert data["summary"]["total"] == 3
    by_id = {r["id"]: r for r in data["results"]}
    # 明显相关：当前失败测试的输出 → keep
    assert by_id["msg_rel"]["verdict"] == "keep"
    # 明显无关：30 步前的 README 概览 → 不保留
    assert by_id["msg_irr"]["verdict"] in ("drop", "truncate")
    # 中间条目不锁死具体 verdict，但必须有合法结构
    mid = by_id["msg_mid"]
    assert mid["verdict"] in ("keep", "truncate", "drop", "review")
    assert set(mid["probabilities"]) == {"keep", "truncate", "drop"}
    assert 0.0 <= mid["margin"] <= 1.0
    assert mid["redundant"] is None or 0.0 <= mid["redundant"] <= 1.0
    assert data["batches"] >= 1
    assert data["usage"]["input_tokens"] > 0
