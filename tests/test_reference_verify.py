"""reference_verify 工具测试。

① mock 测试（默认，不触网）：MockJevAPI + 真实 JevClient + fastmcp in-process Client
   端到端，覆盖详设 §7.3 的验收要点。
② 真实 API 测试（@pytest.mark.real）：真实调用 jev，断言语义符合预期。
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from conftest import make_decide_response

MODULE = "jev_harness_tool.tools.reference_verify"

CLAIM = "Python 3.12 引入了 per-interpreter GIL（PEP 684），允许每个解释器拥有独立的 GIL。"
PASSAGE = (
    "Python 3.12 版本通过 PEP 684 引入了 per-interpreter GIL 机制。"
    "从此每个子解释器可以拥有自己独立的全局解释器锁（GIL），"
    "使得同一进程内的多个解释器能够真正并行利用多核 CPU。"
)


def _unwrap(result):
    if result.data is not None:
        return result.data
    return json.loads(result.content[0].text)


def _probs(winner: str, p_winner: float = 0.85) -> dict:
    rest = ["direct_support", "partial_support", "irrelevant", "contradicts"]
    rest.remove(winner)
    probs = {winner: p_winner}
    remaining = round(1.0 - p_winner, 6)
    per = round(remaining / 3, 6)
    for k in rest:
        probs[k] = per
    return probs


def _response(
    wire_keys: list[str],
    *,
    support: float = 0.9,
    relation: str = "direct_support",
    relation_probs: dict | None = None,
    input_tokens: int = 100,
) -> dict:
    answers: dict = {}
    for k in wire_keys:
        answers[f"{k}_support"] = {"type": "noul", "noul": support}
        probs = relation_probs or _probs(relation)
        answers[f"{k}_relation"] = {
            "type": "choice",
            "choice": relation,
            "confidence": probs[relation],
            "probabilities": probs,
        }
    return {
        "model": "jev-test",
        "answers": answers,
        "usage": {
            "input_tokens": input_tokens,
            "cost_usd": 0.0001,
            "credits_remaining_usd": 4.9,
        },
    }


async def _call_tool(args: dict):
    from fastmcp import Client

    from jev_harness_tool.server import create_server

    async with Client(create_server()) as c:
        result = await c.call_tool("reference_verify", args)
    return result


def _pair(pid: str, claim: str = CLAIM, passage: str = PASSAGE) -> dict:
    return {
        "id": pid,
        "claim": claim,
        "passage": passage,
        "source": {"title": f"src-{pid}", "url": f"https://example.org/{pid}"},
    }


# ---------- mock 测试（不触网） ----------


async def test_batch_reduction_single_request(mock_api, make_client, patched_tool_client):
    """N 对输入只产生 1 次 /decide，questions=2N，wire key 为 p0/p1…，
    外部 id 不出现在请求体且在响应中原样透传。"""
    pairs = [_pair("c1"), _pair("c2"), _pair("c3")]
    mock_api.push(200, _response(["p0", "p1", "p2"]))
    patched_tool_client(make_client(), module=MODULE)

    data = _unwrap(await _call_tool({"pairs": pairs}))

    assert len(mock_api.requests) == 1
    sent = json.loads(mock_api.requests[0].content)
    assert set(sent["questions"].keys()) == {
        f"p{i}_{suffix}" for i in range(3) for suffix in ("support", "relation")
    }
    assert set(sent["state"]["pairs"].keys()) == {"p0", "p1", "p2"}
    raw = mock_api.requests[0].content.decode()
    assert '"c1"' not in raw and '"c2"' not in raw and '"c3"' not in raw  # 外部 id 不进 wire
    assert "src-c1" not in raw  # source 不参与判断
    assert [r["id"] for r in data["results"]] == ["c1", "c2", "c3"]  # 原样透传
    assert all(r["verdict"] == "supported" for r in data["results"])
    assert all(r["action"] == "allow" for r in data["results"])
    assert data["results"][0]["source"] == pairs[0]["source"]  # source 透传
    assert data["summary"]["supported"] == 3
    assert data["stats"]["batches"] == 1


async def test_default_id_is_wire_key(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _response(["p0"]))
    patched_tool_client(make_client(), module=MODULE)

    data = _unwrap(await _call_tool({"pairs": [{"claim": CLAIM, "passage": PASSAGE}]}))

    assert data["results"][0]["id"] == "p0"
    assert data["results"][0]["source"] is None


async def test_contradicts_priority(mock_api, make_client, patched_tool_client):
    """support 高但 relation=contradicts → contradicted/block（矛盾优先）。"""
    mock_api.push(200, _response(["p0"], support=0.95, relation="contradicts"))
    patched_tool_client(make_client(), module=MODULE)

    data = _unwrap(await _call_tool({"pairs": [_pair("x")]}))

    r = data["results"][0]
    assert r["verdict"] == "contradicted"
    assert r["action"] == "block"
    assert r["relation"] == "contradicts"
    assert r["support"] == 0.95


async def test_margin_shortfall_review(mock_api, make_client, patched_tool_client):
    """relation 冠亚军差 < relation_margin → review。"""
    probs = {"direct_support": 0.5, "partial_support": 0.42, "irrelevant": 0.05, "contradicts": 0.03}
    mock_api.push(200, _response(["p0"], relation="direct_support", relation_probs=probs))
    patched_tool_client(make_client(), module=MODULE)

    data = _unwrap(await _call_tool({"pairs": [_pair("x")]}))

    r = data["results"][0]
    assert r["verdict"] == "review"
    assert r["action"] == "review"
    assert r["relation_margin"] == pytest.approx(0.08)


async def test_low_support_unsupported(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _response(["p0"], support=0.5))
    patched_tool_client(make_client(), module=MODULE)

    data = _unwrap(await _call_tool({"pairs": [_pair("x")]}))

    assert data["results"][0]["verdict"] == "unsupported"
    assert data["results"][0]["action"] == "review"


async def test_partial_support(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _response(["p0"], relation="partial_support"))
    patched_tool_client(make_client(), module=MODULE)

    data = _unwrap(await _call_tool({"pairs": [_pair("x")]}))

    r = data["results"][0]
    assert r["verdict"] == "partially_supported"
    assert r["action"] == "review"


async def test_high_support_but_irrelevant_is_conflict(mock_api, make_client, patched_tool_client):
    """support 高但 relation 判 irrelevant → 信号冲突，unsupported/review。"""
    mock_api.push(200, _response(["p0"], support=0.9, relation="irrelevant"))
    patched_tool_client(make_client(), module=MODULE)

    data = _unwrap(await _call_tool({"pairs": [_pair("x")]}))

    assert data["results"][0]["verdict"] == "unsupported"


async def test_truncation_never_auto_pass(mock_api, make_client, patched_tool_client):
    """passage 超 passage_max_chars → 请求体被截断、truncated=true、
    即便 mock 返回 support=0.99 + direct_support 也降级 review。"""
    long_passage = PASSAGE + "补" * 500
    mock_api.push(200, _response(["p0"], support=0.99, relation_probs=_probs("direct_support", 0.99)))
    patched_tool_client(make_client(), module=MODULE)

    data = _unwrap(
        await _call_tool({"pairs": [_pair("t", passage=long_passage)], "passage_max_chars": 100})
    )

    sent = json.loads(mock_api.requests[0].content)
    assert sent["state"]["pairs"]["p0"]["passage"] == long_passage[:100]  # 头部保留截断
    r = data["results"][0]
    assert r["truncated"] is True
    assert r["verdict"] == "review"  # 截断对永不自动通过
    assert r["action"] == "review"
    assert data["thresholds"]["passage_max_chars"] == 100


async def test_truncated_contradicts_still_blocked(mock_api, make_client, patched_tool_client):
    """截断只锁死自动通过；contradicted 照常。"""
    mock_api.push(200, _response(["p0"], support=0.9, relation="contradicts"))
    patched_tool_client(make_client(), module=MODULE)

    data = _unwrap(
        await _call_tool({"pairs": [_pair("t", passage=PASSAGE + "x" * 100)], "passage_max_chars": 50})
    )

    assert data["results"][0]["verdict"] == "contradicted"
    assert data["results"][0]["action"] == "block"


async def test_malformed_answer_only_affects_that_pair(mock_api, make_client, patched_tool_client):
    """单对畸形（noul 越界 / relation 缺失 / choice 非 argmax）只标该对 invalid_response。"""
    resp = _response(["p0", "p1", "p2"])
    resp["answers"]["p1_support"] = {"type": "noul", "noul": 1.5}  # 概率越界
    del resp["answers"]["p2_relation"]  # relation 答案缺失
    resp["answers"]["p0_relation"] = {  # choice 非 argmax
        "type": "choice",
        "choice": "direct_support",
        "probabilities": {"direct_support": 0.3, "partial_support": 0.7, "irrelevant": 0.0, "contradicts": 0.0},
    }
    mock_api.push(200, resp)
    patched_tool_client(make_client(), module=MODULE)

    data = _unwrap(await _call_tool({"pairs": [_pair("a"), _pair("b"), _pair("c")]}))

    assert len(mock_api.requests) == 1  # 不炸 batch
    assert [r["verdict"] for r in data["results"]] == [
        "invalid_response",
        "invalid_response",
        "invalid_response",
    ]
    assert all(r["invalid_reason"] for r in data["results"])
    assert data["summary"]["invalid_response"] == 3


async def test_one_bad_pair_others_survive(mock_api, make_client, patched_tool_client):
    resp = _response(["p0", "p1"])
    resp["answers"]["p1_support"] = "garbage"
    mock_api.push(200, resp)
    patched_tool_client(make_client(), module=MODULE)

    data = _unwrap(await _call_tool({"pairs": [_pair("ok"), _pair("bad")]}))

    assert data["results"][0]["verdict"] == "supported"
    assert data["results"][1]["verdict"] == "invalid_response"
    assert data["results"][1]["support"] is None
    assert data["results"][1]["relation"] is None


async def test_empty_pairs_no_http(mock_api, make_client, patched_tool_client):
    patched_tool_client(make_client(), module=MODULE)

    data = _unwrap(await _call_tool({"pairs": []}))

    assert len(mock_api.requests) == 0  # 不发 HTTP
    assert data["results"] == []
    assert data["summary"] == {
        "total": 0,
        "supported": 0,
        "partially_supported": 0,
        "unsupported": 0,
        "contradicted": 0,
        "review": 0,
        "invalid_response": 0,
    }
    assert data["usage"]["input_tokens"] == 0
    assert data["stats"]["batches"] == 0


async def test_too_many_pairs(mock_api, make_client, patched_tool_client):
    from fastmcp.exceptions import ToolError as ClientToolError

    patched_tool_client(make_client(), module=MODULE)

    with pytest.raises(ClientToolError, match="too_many_pairs"):
        await _call_tool({"pairs": [_pair(f"c{i}") for i in range(3)], "max_pairs": 2})
    assert len(mock_api.requests) == 0


async def test_empty_claim_or_passage_not_sent(mock_api, make_client, patched_tool_client):
    """空 claim/passage 的 pair 直接 invalid_response，不进请求体，其余照常。"""
    mock_api.push(200, _response(["p2"]))
    patched_tool_client(make_client(), module=MODULE)

    pairs = [
        {"id": "e1", "claim": "", "passage": PASSAGE},
        {"id": "e2", "claim": CLAIM, "passage": "   "},
        _pair("ok"),
    ]
    data = _unwrap(await _call_tool({"pairs": pairs}))

    sent = json.loads(mock_api.requests[0].content)
    assert set(sent["state"]["pairs"].keys()) == {"p2"}  # 前两个未发送
    assert set(sent["questions"].keys()) == {"p2_support", "p2_relation"}
    assert data["results"][0]["invalid_reason"] == "empty_claim"
    assert data["results"][1]["invalid_reason"] == "empty_passage"
    assert data["results"][2]["verdict"] == "supported"


async def test_batching_splits_and_aggregates_usage(mock_api, make_client, patched_tool_client):
    """超 64k×0.85 估算的多对输入 → 多批请求，usage 聚合。"""
    pairs = [_pair(f"b{i}", passage="测" * 8000) for i in range(8)]
    mock_api.push(200, _response([f"p{i}" for i in range(8)], input_tokens=1000))
    mock_api.push(200, _response([f"p{i}" for i in range(8)], input_tokens=1000))
    mock_api.push(200, _response([f"p{i}" for i in range(8)], input_tokens=1000))
    patched_tool_client(make_client(), module=MODULE)

    data = _unwrap(await _call_tool({"pairs": pairs, "passage_max_chars": 8000}))

    n_requests = len(mock_api.requests)
    assert n_requests >= 2
    assert data["stats"]["batches"] == n_requests
    assert data["usage"]["input_tokens"] == 1000 * n_requests  # 聚合求和
    assert data["usage"]["credits_remaining_usd"] == 4.9
    assert len(data["results"]) == 8  # 无丢失
    assert [r["id"] for r in data["results"]] == [f"b{i}" for i in range(8)]  # 保序
    assert all(r["verdict"] == "supported" for r in data["results"])
    # 每批请求体都只携带本批 pairs 子集
    seen_keys = set()
    for req in mock_api.requests:
        sent = json.loads(req.content)
        seen_keys.update(sent["state"]["pairs"].keys())
    assert seen_keys == {f"p{i}" for i in range(8)}


async def test_pair_too_large_fail_closed(mock_api, make_client, patched_tool_client):
    """单 pair 截断后仍超 32k×0.85 → pair_too_large，不进任何批次。"""
    huge = "测" * 40_000  # ≈36k est tokens > 27.2k 单问预算
    mock_api.push(200, _response(["p1"]))
    patched_tool_client(make_client(), module=MODULE)

    data = _unwrap(
        await _call_tool(
            {
                "pairs": [_pair("huge", passage=huge), _pair("ok")],
                "passage_max_chars": 50_000,  # 截断阈值放宽，靠预算兜住
            }
        )
    )

    sent = json.loads(mock_api.requests[0].content)
    assert set(sent["state"]["pairs"].keys()) == {"p1"}
    assert data["results"][0]["invalid_reason"] == "pair_too_large"
    assert data["results"][0]["verdict"] == "invalid_response"
    assert data["results"][1]["verdict"] == "supported"


async def test_401_not_retried_surfaces_error(mock_api, make_client, patched_tool_client):
    from fastmcp.exceptions import ToolError as ClientToolError

    mock_api.push(401, {"error": "invalid key"})
    patched_tool_client(make_client(), module=MODULE)

    with pytest.raises(ClientToolError, match="JevAuthError"):
        await _call_tool({"pairs": [_pair("x")]})
    assert len(mock_api.requests) == 1


async def test_502_retried_then_success(mock_api, make_client, patched_tool_client):
    mock_api.push(502, {"error": "upstream"}).push(502, {"error": "upstream"})
    mock_api.push(200, _response(["p0"]))
    patched_tool_client(make_client(), module=MODULE)

    data = _unwrap(await _call_tool({"pairs": [_pair("x")]}))

    assert len(mock_api.requests) == 3
    assert data["results"][0]["verdict"] == "supported"


async def test_network_error_never_resent_batch_fail_closed(mock_api, make_client, patched_tool_client):
    """歧义网络错误绝不重发；该批全部 pair 标 transport_error，调用本身不抛错。"""
    mock_api.push_error(httpx.ConnectError("connection reset by peer"))
    patched_tool_client(make_client(max_retries=5), module=MODULE)

    data = _unwrap(await _call_tool({"pairs": [_pair("n1"), _pair("n2")]}))

    assert len(mock_api.requests) == 1  # 不重发
    assert all(r["verdict"] == "invalid_response" for r in data["results"])
    assert all(r["invalid_reason"] == "transport_error" for r in data["results"])
    assert data["usage"]["input_tokens"] == 0


async def test_thresholds_and_usage_echo(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _response(["p0"], input_tokens=777))
    patched_tool_client(make_client(), module=MODULE)

    data = _unwrap(
        await _call_tool(
            {"pairs": [_pair("x")], "support_threshold": 0.6, "relation_margin": 0.2}
        )
    )

    assert data["thresholds"] == {
        "support_threshold": 0.6,
        "relation_margin": 0.2,
        "passage_max_chars": 8000,
        "context_max_chars": 8000,
    }
    assert data["usage"]["input_tokens"] == 777
    assert data["usage"]["cost_usd"] == pytest.approx(0.0001)
    assert data["stats"]["duration_ms"] >= 0
    assert data["stats"]["pairs_total"] == 1


async def test_custom_threshold_changes_verdict(mock_api, make_client, patched_tool_client):
    """support=0.65：默认阈值 0.7 下 unsupported；调低到 0.6 后 supported。"""
    mock_api.push(200, _response(["p0"], support=0.65))
    mock_api.push(200, _response(["p0"], support=0.65))
    patched_tool_client(make_client(), module=MODULE)

    strict = _unwrap(await _call_tool({"pairs": [_pair("x")]}))
    loose = _unwrap(await _call_tool({"pairs": [_pair("x")], "support_threshold": 0.6}))

    assert strict["results"][0]["verdict"] == "unsupported"
    assert loose["results"][0]["verdict"] == "supported"


# ---------- context（passage 所在段落上下文） ----------


async def test_context_carried_into_wire_state(mock_api, make_client, patched_tool_client):
    """传入 context 时出现在 wire state 的对应 pair 中。"""
    mock_api.push(200, _response(["p0"]))
    patched_tool_client(make_client(), module=MODULE)

    pair = {**_pair("c"), "context": "该机制自 3.12 起默认启用，适用于全部子解释器。"}
    data = _unwrap(await _call_tool({"pairs": [pair]}))

    sent = json.loads(mock_api.requests[0].content)
    assert sent["state"]["pairs"]["p0"] == {
        "claim": CLAIM,
        "passage": PASSAGE,
        "context": "该机制自 3.12 起默认启用，适用于全部子解释器。",
    }
    assert data["results"][0]["verdict"] == "supported"


async def test_no_context_wire_state_unchanged(mock_api, make_client, patched_tool_client):
    """不传 context → state 中无 context 键（向后兼容，wire 与旧版一致）。"""
    mock_api.push(200, _response(["p0"]))
    patched_tool_client(make_client(), module=MODULE)

    _unwrap(await _call_tool({"pairs": [_pair("x")]}))

    sent = json.loads(mock_api.requests[0].content)
    assert sent["state"]["pairs"]["p0"] == {"claim": CLAIM, "passage": PASSAGE}


async def test_context_empty_or_non_str_treated_as_absent(mock_api, make_client, patched_tool_client):
    """context 为空串/空白/非 str → 按未提供处理：正常发送、state 无 context 键、无 invalid。"""
    mock_api.push(200, _response(["p0", "p1", "p2"]))
    patched_tool_client(make_client(), module=MODULE)

    pairs = [
        {**_pair("empty"), "context": ""},
        {**_pair("blank"), "context": "   "},
        {**_pair("num"), "context": 123},
    ]
    data = _unwrap(await _call_tool({"pairs": pairs}))

    sent = json.loads(mock_api.requests[0].content)
    for key in ("p0", "p1", "p2"):
        assert "context" not in sent["state"]["pairs"][key]
    assert all(r["verdict"] == "supported" for r in data["results"])
    assert all(r["invalid_reason"] is None for r in data["results"])


async def test_mixed_pairs_with_and_without_context(mock_api, make_client, patched_tool_client):
    """同批混合有/无 context 的 pair → 单次 /decide，state 中各自形态正确。"""
    mock_api.push(200, _response(["p0", "p1"]))
    patched_tool_client(make_client(), module=MODULE)

    pairs = [_pair("plain"), {**_pair("ctx"), "context": "补充上下文段落。"}]
    _unwrap(await _call_tool({"pairs": pairs}))

    assert len(mock_api.requests) == 1
    sent = json.loads(mock_api.requests[0].content)
    assert "context" not in sent["state"]["pairs"]["p0"]
    assert sent["state"]["pairs"]["p1"]["context"] == "补充上下文段落。"


async def test_context_truncation_never_auto_pass(mock_api, make_client, patched_tool_client):
    """context 超 context_max_chars → 请求体被截断、truncated=true、
    即便 mock 返回 support=0.99 + direct_support 也降级 review。"""
    long_context = "上下文段落。" + "补" * 500
    mock_api.push(200, _response(["p0"], support=0.99, relation_probs=_probs("direct_support", 0.99)))
    patched_tool_client(make_client(), module=MODULE)

    pair = {**_pair("t"), "context": long_context}
    data = _unwrap(await _call_tool({"pairs": [pair], "context_max_chars": 100}))

    sent = json.loads(mock_api.requests[0].content)
    assert sent["state"]["pairs"]["p0"]["context"] == long_context[:100]  # 头部保留截断
    r = data["results"][0]
    assert r["truncated"] is True
    assert r["verdict"] == "review"  # 截断对永不自动通过
    assert r["action"] == "review"
    assert data["thresholds"]["context_max_chars"] == 100


async def test_context_contributes_to_budget_batching(mock_api, make_client, patched_tool_client):
    """大 context 计入 state_tokens 估算，推动分批。"""
    pairs = [{**_pair(f"b{i}"), "context": "测" * 8000} for i in range(8)]
    mock_api.push(200, _response([f"p{i}" for i in range(8)], input_tokens=1000))
    mock_api.push(200, _response([f"p{i}" for i in range(8)], input_tokens=1000))
    mock_api.push(200, _response([f"p{i}" for i in range(8)], input_tokens=1000))
    patched_tool_client(make_client(), module=MODULE)

    data = _unwrap(await _call_tool({"pairs": pairs, "context_max_chars": 8000}))

    n_requests = len(mock_api.requests)
    assert n_requests >= 2
    assert data["stats"]["batches"] == n_requests
    assert len(data["results"]) == 8
    seen_keys = set()
    for req in mock_api.requests:
        sent = json.loads(req.content)
        seen_keys.update(sent["state"]["pairs"].keys())
    assert seen_keys == {f"p{i}" for i in range(8)}


# ---------- 真实 API 测试（烧 credits，默认不跑） ----------


@pytest.mark.real
async def test_real_reference_verify_supported_and_irrelevant():
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    pairs = [
        {
            "id": "gil",
            "claim": CLAIM,
            "passage": PASSAGE,
            "source": {"title": "Python 3.12 release notes", "url": "https://example.org/py312"},
        },
        {
            "id": "unrelated",
            "claim": CLAIM,
            "passage": (
                "法国大革命始于 1789 年，攻占巴士底狱成为其标志性事件。"
                "革命推翻了波旁王朝的君主专制，深刻改变了欧洲的政治格局。"
            ),
        },
    ]
    data = _unwrap(await _call_tool({"pairs": pairs}))

    by_id = {r["id"]: r for r in data["results"]}
    gil = by_id["gil"]
    assert gil["verdict"] == "supported"  # 明确支持的 passage 必须自动通过
    assert gil["action"] == "allow"
    assert gil["relation"] == "direct_support"
    assert gil["support"] is not None and gil["support"] >= 0.7
    unrelated = by_id["unrelated"]
    assert unrelated["verdict"] == "unsupported"  # 明显无关的 passage 不得通过
    assert unrelated["action"] == "review"
    assert data["usage"]["input_tokens"] > 0
    assert data["summary"]["total"] == 2
    assert data["stats"]["batches"] >= 1
