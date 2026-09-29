"""skill_search 工具测试。

① mock 测试（默认，不触网）：MockJevAPI + patched_tool_client 注入，覆盖
   multi/choice 问题构造、verdict 分支、invalid_response、截断、分批、
   重试/不重发、参数校验、空候选。
② 真实 API 测试（@pytest.mark.real）：fastmcp in-process Client 真实调用。
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from conftest import MockJevAPI, make_decide_response
from jev_harness_tool.core import tokens as tokens_mod

MODULE = "jev_harness_tool.tools.skill_search"

SKILLS_3 = [
    {"name": "ppt-architect", "description": "Create presentation slide decks from scratch."},
    {"name": "pdf", "description": "Read, extract, merge and split PDF files."},
    {"name": "db-migrate", "description": "Database schema migration helpers."},
]


def _unwrap(result):
    if result.data is not None:
        return result.data
    return json.loads(result.content[0].text)


def _usage(input_tokens: int = 100) -> dict:
    return {
        "input_tokens": input_tokens,
        "cost_usd": 0.00001,
        "credits_remaining_usd": 4.99,
    }


def _noul_body(probs: dict[str, float], input_tokens: int = 100) -> dict:
    return {
        "model": "jev-test",
        "answers": {k: {"type": "noul", "noul": v} for k, v in probs.items()},
        "usage": _usage(input_tokens),
    }


def _choice_body(probs: dict[str, float], exists_p: float, input_tokens: int = 100) -> dict:
    winner = max(probs, key=lambda k: probs[k])
    return {
        "model": "jev-test",
        "answers": {
            "pick": {
                "type": "choice",
                "choice": winner,
                "confidence": probs[winner],
                "probabilities": probs,
            },
            "exists": {"type": "noul", "noul": exists_p},
        },
        "usage": _usage(input_tokens),
    }


async def _call(skills, **kwargs):
    from fastmcp import Client

    from jev_harness_tool.server import create_server

    arguments = {"query": kwargs.pop("query", "帮我做一份 PPT"), "skills": skills}
    arguments.update(kwargs)
    async with Client(create_server()) as c:
        result = await c.call_tool("skill_search", arguments)
    return _unwrap(result)


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


async def _no_sleep(_seconds: float) -> None:
    return None


def _routed_client(route, **overrides):
    from jev_harness_tool.core.client import JevClient

    from conftest import make_settings

    routed = RoutedMockAPI(route)
    client = JevClient(make_settings(**overrides), transport=routed.transport, sleep=_no_sleep)
    return routed, client


# ---------- multi 模式 ----------


async def test_multi_happy_path(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _noul_body({"s0": 0.9, "s1": 0.5, "s2": 0.1}))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(SKILLS_3)

    assert data["mode"] == "multi"
    assert [s["name"] for s in data["selected"]] == ["ppt-architect", "pdf"]
    assert [s["probability"] for s in data["selected"]] == [0.9, 0.5]
    assert all(s["verdict"] == "select" and s["truncated"] is False for s in data["selected"])
    assert [s["name"] for s in data["skipped"]] == ["db-migrate"]
    assert data["invalid"] == []
    # none_probability = (1-0.9)(1-0.5)(1-0.1)
    assert data["none_probability"] == pytest.approx(0.045)
    assert data["batches"] == 1
    assert data["usage"]["input_tokens"] == 100
    assert data["thresholds"]["threshold"] == 0.3
    assert isinstance(data["latency_ms"], int)

    # 请求构造：单批、state 共享、每技能一个位置 key noul（原则 8/15）
    assert len(mock_api.requests) == 1
    sent = json.loads(mock_api.requests[0].content)
    assert sent["state"]["query"] == "帮我做一份 PPT"
    assert [c["key"] for c in sent["state"]["catalog"]] == ["s0", "s1", "s2"]
    assert sent["state"]["catalog"][0]["name"] == "ppt-architect"
    assert set(sent["questions"]) == {"s0", "s1", "s2"}
    assert all(q["type"] == "noul" for q in sent["questions"].values())
    assert '"ppt-architect"' in sent["questions"]["s0"]["instructions"]  # name 内联进问题


async def test_multi_truncated_never_auto_select(mock_api, make_client, patched_tool_client):
    long_desc = "x" * 100
    skills = [
        {"name": "long-skill", "description": long_desc},
        {"name": "short-skill", "description": "short"},
    ]
    mock_api.push(200, _noul_body({"s0": 0.9, "s1": 0.9}))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(skills, description_max_chars=10)

    sent = json.loads(mock_api.requests[0].content)
    assert len(sent["state"]["catalog"][0]["description"]) == 10  # 请求中已截断
    # 概率达标但 description 被截断 → review，不自动 select（原则 12）
    assert [s["name"] for s in data["selected"]] == ["short-skill"]
    assert data["reviewed"][0]["name"] == "long-skill"
    assert data["reviewed"][0]["reason"] == "truncated"
    assert data["reviewed"][0]["truncated"] is True


async def test_multi_over_max_results_goes_to_review(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _noul_body({"s0": 0.9, "s1": 0.8, "s2": 0.7}))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(SKILLS_3, max_results=2)

    assert [s["name"] for s in data["selected"]] == ["ppt-architect", "pdf"]
    assert len(data["reviewed"]) == 1
    assert data["reviewed"][0]["name"] == "db-migrate"
    assert data["reviewed"][0]["reason"] == "over_max_results"


async def test_multi_invalid_answer_isolated(mock_api, make_client, patched_tool_client):
    body = _noul_body({"s0": 0.9, "s2": 0.1})
    body["answers"]["s1"] = {"type": "noul", "noul": 1.5}  # 概率越界
    mock_api.push(200, body)
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(SKILLS_3)

    # 单个坏答案只影响该项，不炸掉同批其他技能（原则 13）
    assert [s["name"] for s in data["selected"]] == ["ppt-architect"]
    assert [s["name"] for s in data["skipped"]] == ["db-migrate"]
    assert len(data["invalid"]) == 1
    assert data["invalid"][0]["name"] == "pdf"
    assert data["invalid"][0]["verdict"] == "invalid_response"
    # none_probability 仅对有效答案计算：(1-0.9)(1-0.1)
    assert data["none_probability"] == pytest.approx(0.09)


async def test_multi_missing_answer_and_wrong_type(mock_api, make_client, patched_tool_client):
    body = _noul_body({"s2": 0.1})  # s0 缺失
    body["answers"]["s1"] = {"type": "choice", "choice": "a", "probabilities": {"a": 1.0}}
    mock_api.push(200, body)
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(SKILLS_3)

    reasons = {i["name"]: i["reason"] for i in data["invalid"]}
    assert reasons == {"ppt-architect": "missing answer", "pdf": "answer type is not noul"}
    assert data["selected"] == []
    assert [s["name"] for s in data["skipped"]] == ["db-migrate"]


async def test_multi_all_invalid_none_probability_null(mock_api, make_client, patched_tool_client):
    mock_api.push(200, {"model": "jev-test", "answers": {}, "usage": _usage()})
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(SKILLS_3[:1])

    assert len(data["invalid"]) == 1
    assert data["none_probability"] is None


async def test_empty_skills_no_request(mock_api, make_client, patched_tool_client):
    patched_tool_client(make_client(), module=MODULE)

    data = await _call([])

    assert data["selected"] == [] and data["reviewed"] == [] and data["skipped"] == []
    assert data["none_probability"] is None
    assert data["batches"] == 0
    assert data["usage"]["input_tokens"] == 0
    assert len(mock_api.requests) == 0  # 不发请求


async def test_multi_batching_merge_and_usage_sum(patched_tool_client, monkeypatch):
    monkeypatch.setattr(tokens_mod, "TOTAL_TOKEN_BUDGET", 300)  # 强制分批

    def route(body):
        answers = {name: {"type": "noul", "noul": {"s0": 0.9, "s1": 0.6, "s2": 0.2}[name]}
                   for name in body["questions"]}
        return 200, {"model": "jev-test", "answers": answers, "usage": _usage(10)}

    routed, client = _routed_client(route)
    patched_tool_client(client, module=MODULE)

    data = await _call(SKILLS_3)

    assert data["batches"] == 3  # 每批 1 个技能
    assert len(routed.requests) == 3
    # 跨批合并后按概率全局排序
    assert [s["name"] for s in data["selected"]] == ["ppt-architect", "pdf"]
    assert [s["name"] for s in data["skipped"]] == ["db-migrate"]
    # 位置 key 全局连续，不随分批重置
    keys = sorted(
        c["key"] for r in routed.requests for c in json.loads(r.content)["state"]["catalog"]
    )
    assert keys == ["s0", "s1", "s2"]
    # 每批 state 重发（含 query）
    assert all(json.loads(r.content)["state"]["query"] for r in routed.requests)
    # usage 各批求和
    assert data["usage"]["input_tokens"] == 30


async def test_multi_batch_failure_isolated(patched_tool_client):
    def route(body):
        if "s0" in body["questions"]:
            return 502, {"error": "upstream"}
        answers = {name: {"type": "noul", "noul": 0.9} for name in body["questions"]}
        return 200, {"model": "jev-test", "answers": answers, "usage": _usage(10)}

    routed, client = _routed_client(route, max_retries=1)
    patched_tool_client(client, module=MODULE)

    # 用 max_skills_per_batch 强制分两批：s0 一批、其余一批
    data = await _call(SKILLS_3, max_skills_per_batch=1)

    assert data["batches"] == 3
    # s0 所在批失败 → invalid_response（upstream）；其他批正常
    assert [i["name"] for i in data["invalid"]] == ["ppt-architect"]
    assert data["invalid"][0]["reason"] == "upstream_error"
    assert {s["name"] for s in data["selected"]} == {"pdf", "db-migrate"}


async def test_retry_502_then_success(mock_api, make_client, patched_tool_client):
    mock_api.push(502, {"error": "upstream"}).push(200, _noul_body({"s0": 0.9}))
    patched_tool_client(make_client(max_retries=3), module=MODULE)

    data = await _call(SKILLS_3[:1])

    assert data["selected"][0]["name"] == "ppt-architect"
    assert len(mock_api.requests) == 2  # 指数退避重试成功


async def test_network_error_never_resent(mock_api, make_client, patched_tool_client):
    mock_api.push_error(httpx.ConnectError("connection reset by peer"))
    patched_tool_client(make_client(max_retries=5), module=MODULE)

    data = await _call(SKILLS_3[:1])

    assert len(mock_api.requests) == 1  # 歧义网络错误绝不重发
    assert data["invalid"][0]["reason"] == "network_ambiguous"
    assert data["selected"] == []
    assert data["none_probability"] is None


async def test_401_surfaces_tool_error(mock_api, make_client, patched_tool_client):
    from fastmcp import Client
    from fastmcp.exceptions import ToolError as ClientToolError

    from jev_harness_tool.server import create_server

    mock_api.push(401, {"error": "invalid key"})
    patched_tool_client(make_client(), module=MODULE)

    async with Client(create_server()) as c:
        with pytest.raises(ClientToolError, match="JevAuthError"):
            await c.call_tool("skill_search", {"query": "q", "skills": SKILLS_3})
    assert len(mock_api.requests) == 1  # 零重试


# ---------- choice 模式 ----------


async def test_choice_happy_path(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _choice_body({"s0": 0.7, "s1": 0.2, "s2": 0.1}, exists_p=0.9))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(SKILLS_3, mode="choice")

    assert [s["name"] for s in data["selected"]] == ["ppt-architect"]
    assert data["selected"][0]["probability"] == pytest.approx(0.7)
    assert data["detail"]["overall_verdict"] == "auto_select"
    assert data["detail"]["exists_probability"] == pytest.approx(0.9)
    assert data["none_probability"] == pytest.approx(0.1)
    assert data["batches"] == 1

    # 同一 /decide 调用内并行 pick + exists（原则 3/8）
    assert len(mock_api.requests) == 1
    sent = json.loads(mock_api.requests[0].content)
    assert set(sent["questions"]) == {"pick", "exists"}
    assert sent["questions"]["pick"]["type"] == "choice"
    assert list(sent["questions"]["pick"]["criteria"]) == ["s0", "s1", "s2"]
    assert sent["questions"]["exists"]["type"] == "noul"


async def test_choice_exists_gate_none_relevant(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _choice_body({"s0": 0.7, "s1": 0.2, "s2": 0.1}, exists_p=0.1))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(SKILLS_3, mode="choice")

    # 存在性门控：choice 分布作废，不产出 select（原则 3）
    assert data["selected"] == []
    assert data["detail"]["overall_verdict"] == "none_relevant"
    assert data["none_probability"] == pytest.approx(0.9)
    assert len(data["skipped"]) == 3
    assert all(s["reason"] == "none_relevant" for s in data["skipped"])


async def test_choice_low_margin_reviews_top_two(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _choice_body({"s0": 0.45, "s1": 0.4, "s2": 0.15}, exists_p=0.9))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(SKILLS_3, mode="choice")

    # margin 0.05 < 0.15：冠亚军均 review，绝不自动二选一（原则 5）
    assert data["selected"] == []
    assert data["detail"]["overall_verdict"] == "low_margin"
    assert [r["name"] for r in data["reviewed"]] == ["ppt-architect", "pdf"]
    assert all(r["reason"] == "low_margin" for r in data["reviewed"])
    assert [s["name"] for s in data["skipped"]] == ["db-migrate"]


async def test_choice_below_threshold_reviews_winner(mock_api, make_client, patched_tool_client):
    mock_api.push(200, _choice_body({"s0": 0.25, "s1": 0.2, "s2": 0.1}, exists_p=0.9))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(SKILLS_3, mode="choice")

    assert data["selected"] == []
    assert data["detail"]["overall_verdict"] == "below_threshold"
    assert data["reviewed"][0]["name"] == "ppt-architect"
    assert data["reviewed"][0]["reason"] == "below_threshold"


async def test_choice_truncated_winner_demoted_to_review(mock_api, make_client, patched_tool_client):
    skills = [{"name": "long", "description": "x" * 100}] + SKILLS_3[1:]
    mock_api.push(200, _choice_body({"s0": 0.8, "s1": 0.15, "s2": 0.05}, exists_p=0.9))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(skills, mode="choice", description_max_chars=10)

    assert data["selected"] == []
    assert data["reviewed"][0]["name"] == "long"
    assert data["reviewed"][0]["reason"] == "truncated"


async def test_choice_invalid_pick_fails_whole_result(mock_api, make_client, patched_tool_client):
    body = _choice_body({"s0": 0.7, "s1": 0.2, "s2": 0.1}, exists_p=0.9)
    body["answers"]["pick"]["choice"] = "s1"  # 非 argmax
    mock_api.push(200, body)
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(SKILLS_3, mode="choice")

    # choice 是单点决策，无法逐项降级 → 全部 invalid
    assert len(data["invalid"]) == 3
    assert all(i["verdict"] == "invalid_response" for i in data["invalid"])
    assert data["selected"] == [] and data["none_probability"] is None


async def test_choice_over_255_skills_rejected(make_client, patched_tool_client):
    from fastmcp import Client
    from fastmcp.exceptions import ToolError as ClientToolError

    from jev_harness_tool.server import create_server

    patched_tool_client(make_client(), module=MODULE)
    skills = [{"name": f"skill-{i}", "description": "d"} for i in range(256)]

    async with Client(create_server()) as c:
        with pytest.raises(ClientToolError, match="255"):
            await c.call_tool(
                "skill_search", {"query": "q", "skills": skills, "mode": "choice"}
            )


# ---------- 参数校验 ----------


async def test_invalid_threshold_rejected(make_client, patched_tool_client):
    from fastmcp import Client
    from fastmcp.exceptions import ToolError as ClientToolError

    from jev_harness_tool.server import create_server

    patched_tool_client(make_client(), module=MODULE)

    async with Client(create_server()) as c:
        with pytest.raises(ClientToolError, match="threshold"):
            await c.call_tool(
                "skill_search", {"query": "q", "skills": SKILLS_3, "threshold": 1.5}
            )
        with pytest.raises(ClientToolError, match="mode"):
            await c.call_tool(
                "skill_search", {"query": "q", "skills": SKILLS_3, "mode": "bogus"}
            )
        with pytest.raises(ClientToolError, match="query"):
            await c.call_tool("skill_search", {"query": "  ", "skills": SKILLS_3})
        with pytest.raises(ClientToolError, match="name"):
            await c.call_tool(
                "skill_search", {"query": "q", "skills": [{"name": "", "description": "d"}]}
            )


# ---------- 真实 API 测试（烧 credits，默认不跑） ----------

REAL_QUERY = "帮我做一份产品发布会的 PPT"
REAL_SKILLS = [
    {"name": "ppt-architect", "description": "Create presentation slide decks, pitch decks and .pptx files from scratch, from an idea, speech draft, topic, or research report."},
    {"name": "html-ppt", "description": "Author professional static HTML presentations with templates, many styles, layouts and animations."},
    {"name": "pdf-toolkit", "description": "Read, extract, merge and split PDF files."},
    {"name": "db-migrate", "description": "Plan and execute relational database schema migrations (SQL)."},
    {"name": "docker-ops", "description": "Manage Docker containers, images and compose stacks."},
    {"name": "git-history", "description": "Analyze git commit history, blame and code authorship."},
    {"name": "stock-screener", "description": "Screen stocks by financial metrics and valuation ratios."},
    {"name": "translator", "description": "Translate text between languages preserving tone."},
    {"name": "web-scraper", "description": "Crawl websites and extract structured data from pages."},
    {"name": "sql-optimizer", "description": "Analyze and optimize SQL query execution plans."},
]
CLEARLY_IRRELEVANT = {
    "pdf-toolkit", "db-migrate", "docker-ops", "git-history",
    "stock-screener", "translator", "web-scraper", "sql-optimizer",
}


@pytest.mark.real
async def test_real_skill_search_multi_ppt():
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(REAL_SKILLS, query=REAL_QUERY)

    selected_names = {s["name"] for s in data["selected"]}
    assert "ppt-architect" in selected_names
    winner = next(s for s in data["selected"] if s["name"] == "ppt-architect")
    assert winner["probability"] >= 0.3
    assert CLEARLY_IRRELEVANT.isdisjoint(selected_names)
    assert data["invalid"] == []
    assert data["batches"] >= 1
    assert data["usage"]["input_tokens"] > 0
    assert data["none_probability"] is not None


@pytest.mark.real
async def test_real_skill_search_choice_ppt():
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(REAL_SKILLS, query=REAL_QUERY, mode="choice")

    assert data["invalid"] == []
    assert data["detail"]["exists_probability"] >= 0.3  # 存在性门控应放行
    # 冠军应在两个 PPT 技能中产生（select 或 low_margin review 均可接受）
    contenders = {s["name"] for s in data["selected"] + data["reviewed"]}
    assert contenders & {"ppt-architect", "html-ppt"}
    assert data["usage"]["input_tokens"] > 0
