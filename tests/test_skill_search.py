"""skill_search 工具测试。

① mock 测试（默认，不触网）：MockJevAPI + patched_tool_client 注入，覆盖
   auto 单步/两步的问题构造、verdict 分支、invalid_response、截断、分批、
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


def _auto_pick_body(probs: dict[str, float], input_tokens: int = 100) -> dict:
    """auto 单步 mock 响应：pick choice (含 __no_skill_needed__)。"""
    full_probs = dict(probs)
    full_probs["__no_skill_needed__"] = 0.0
    winner = max(full_probs, key=lambda k: full_probs[k])
    return {
        "model": "jev-test",
        "answers": {
            "pick": {
                "type": "choice",
                "choice": winner,
                "confidence": full_probs[winner],
                "probabilities": full_probs,
            },
        },
        "usage": _usage(input_tokens),
    }


def _auto_pick_body_with_none(probs: dict[str, float], none_p: float, input_tokens: int = 100) -> dict:
    """auto mock 响应：pick choice 含 __no_skill_needed__ 的概率。"""
    full_probs = dict(probs)
    full_probs["__no_skill_needed__"] = none_p
    winner = max(full_probs, key=lambda k: full_probs[k])
    return {
        "model": "jev-test",
        "answers": {
            "pick": {
                "type": "choice",
                "choice": winner,
                "confidence": full_probs[winner],
                "probabilities": full_probs,
            },
        },
        "usage": _usage(input_tokens),
    }


def _auto_rerank_body(
    probs: dict[str, float], fits: dict[str, float], none_p: float = 0.0, input_tokens: int = 100
) -> dict:
    """auto step2 mock 响应：pick choice (含 __no_skill_needed__) + per-candidate fits noul。"""
    full_probs = dict(probs)
    full_probs["__no_skill_needed__"] = none_p
    winner = max(full_probs, key=lambda k: full_probs[k])
    answers = {
        "pick": {
            "type": "choice",
            "choice": winner,
            "confidence": full_probs[winner],
            "probabilities": full_probs,
        },
    }
    for k, v in fits.items():
        answers[f"fits::{k}"] = {"type": "noul", "noul": v}
    return {
        "model": "jev-test",
        "answers": answers,
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


# ---------- auto 模式 ----------
# auto 单步：1 choice (pick)，criteria 内嵌 __no_skill_needed__ 选项（无 gate noul）
# auto 两步：step1 noul 召回 → step2 choice (内嵌 __no_skill_needed__) + fits noul
# 默认 exists_threshold=0.3, fits_threshold=0.3


async def test_auto_single_step_happy_path(mock_api, make_client, patched_tool_client):
    """auto 单步：skills ≤ switch_threshold → 1 choice (pick, 含 __no_skill_needed__)。"""
    mock_api.push(200, _auto_pick_body({"s0": 0.70, "s1": 0.20, "s2": 0.10}))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(SKILLS_3)  # 3 skills ≤ 50

    assert data["mode"] == "auto"
    assert data["steps"] == 1
    assert data["selected"][0]["name"] == "ppt-architect"
    assert data["detail"]["overall_verdict"] == "auto_select"
    assert data["none_probability"] == pytest.approx(0.0, abs=0.001)
    assert data["batches"] == 1

    # 问题构造：只有 pick choice（无 gate），criteria 含 __no_skill_needed__
    assert len(mock_api.requests) == 1
    sent = json.loads(mock_api.requests[0].content)
    assert set(sent["questions"]) == {"pick"}
    assert sent["questions"]["pick"]["type"] == "choice"
    assert "__no_skill_needed__" in sent["questions"]["pick"]["criteria"]


async def test_auto_single_step_no_skill_wins(mock_api, make_client, patched_tool_client):
    """__no_skill_needed__ 胜出 → none_relevant，全部 skip。"""
    mock_api.push(200, _auto_pick_body_with_none({"s0": 0.15, "s1": 0.10, "s2": 0.05}, none_p=0.70))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(SKILLS_3)

    assert data["selected"] == []
    assert data["detail"]["overall_verdict"] == "none_relevant"
    assert data["none_probability"] == pytest.approx(0.70, abs=0.001)
    assert len(data["skipped"]) == 3
    assert all(s["reason"] == "none_relevant" for s in data["skipped"])


async def test_auto_single_step_low_margin(mock_api, make_client, patched_tool_client):
    """margin 不足 → 冠亚军 review。"""
    mock_api.push(200, _auto_pick_body_with_none({"s0": 0.45, "s1": 0.40, "s2": 0.12}, none_p=0.03))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(SKILLS_3)

    assert data["selected"] == []
    assert data["detail"]["overall_verdict"] == "low_margin"
    assert [r["name"] for r in data["reviewed"]] == ["ppt-architect", "pdf"]
    assert all(r["reason"] == "low_margin" for r in data["reviewed"])


async def test_auto_single_step_invalid_pick_fails_whole(mock_api, make_client, patched_tool_client):
    """单步 auto: pick 非 argmax → 全部 invalid。"""
    body = _auto_pick_body({"s0": 0.7, "s1": 0.2, "s2": 0.1})
    body["answers"]["pick"]["choice"] = "s1"  # 非 argmax
    mock_api.push(200, body)
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(SKILLS_3)

    assert len(data["invalid"]) == 3
    assert all(i["verdict"] == "invalid_response" for i in data["invalid"])
    assert data["none_probability"] is None


async def test_auto_two_step_recall_rerank(patched_tool_client):
    """skills > switch_threshold → 两步：step1 noul 召回 → step2 choice (含 __no_skill_needed__) + fits。"""
    # 用 switch_threshold=2 触发两步，3 个技能 > 2
    def route(body):
        questions = body["questions"]
        if "pick" in questions:
            # Step 2: choice (含 __no_skill_needed__) + fits
            pick_probs = {}
            fits_answers = {}
            for k in questions["pick"]["criteria"]:
                if k == "__no_skill_needed__":
                    pick_probs[k] = 0.0
                else:
                    pick_probs[k] = {"s0": 0.7, "s1": 0.2, "s2": 0.1}.get(k, 0.1)
                if k != "__no_skill_needed__":
                    fits_answers[f"fits::{k}"] = {
                        "type": "noul",
                        "noul": {"s0": 0.8, "s1": 0.3, "s2": 0.1}.get(k, 0.1),
                    }
            winner = max(pick_probs, key=lambda k: pick_probs[k])
            return 200, {
                "model": "jev-test",
                "answers": {
                    "pick": {
                        "type": "choice",
                        "choice": winner,
                        "confidence": pick_probs[winner],
                        "probabilities": pick_probs,
                    },
                    **fits_answers,
                },
                "usage": _usage(50),
            }
        else:
            # Step 1: noul per skill（无 gate）
            answers = {
                name: {"type": "noul", "noul": {"s0": 0.9, "s1": 0.6, "s2": 0.2}[name]}
                for name in questions
                if name.startswith("s")
            }
            return 200, {"model": "jev-test", "answers": answers, "usage": _usage(100)}

    routed, client = _routed_client(route)
    patched_tool_client(client, module=MODULE)

    data = await _call(SKILLS_3, switch_threshold=2)

    assert data["mode"] == "auto"
    assert data["steps"] == 2
    assert data["selected"][0]["name"] == "ppt-architect"
    assert data["detail"]["overall_verdict"] == "auto_select"
    assert data["detail"]["step1"]["candidates"] == ["s0", "s1", "s2"]
    assert "s0" in data["detail"]["step2"]["fits"]
    # 2 步请求
    assert data["batches"] >= 2
    # usage = step1 + step2
    assert data["usage"]["input_tokens"] >= 150


async def test_auto_two_step_recall_low_exit(patched_tool_client):
    """step1 所有召回概率 < exists_threshold → 跳过 step2。"""
    def route(body):
        questions = body["questions"]
        if "pick" in questions:
            assert False, "step2 should not be reached"
        # 所有技能召回概率很低
        answers = {
            name: {"type": "noul", "noul": {"s0": 0.15, "s1": 0.10, "s2": 0.05}[name]}
            for name in questions
            if name.startswith("s")
        }
        return 200, {"model": "jev-test", "answers": answers, "usage": _usage(100)}

    routed, client = _routed_client(route)
    patched_tool_client(client, module=MODULE)

    data = await _call(SKILLS_3, switch_threshold=2)

    assert data["selected"] == []
    assert data["detail"]["overall_verdict"] == "none_relevant"
    assert data["steps"] == 1  # 只有 step1 执行
    # 没有发起 step2 请求
    assert all("pick" not in json.loads(r.content)["questions"] for r in routed.requests)


async def test_auto_two_step_none_fit(patched_tool_client):
    """step2 all fits < fits_threshold → none_fit。"""
    def route(body):
        questions = body["questions"]
        if "pick" in questions:
            pick_probs = {}
            fits_answers = {}
            for k in questions["pick"]["criteria"]:
                if k == "__no_skill_needed__":
                    pick_probs[k] = 0.0
                else:
                    pick_probs[k] = {"s0": 0.7, "s1": 0.2, "s2": 0.1}.get(k, 0.1)
                if k != "__no_skill_needed__":
                    # 全部 fits 很低
                    fits_answers[f"fits::{k}"] = {"type": "noul", "noul": 0.1}
            winner = max(pick_probs, key=lambda k: pick_probs[k])
            return 200, {
                "model": "jev-test",
                "answers": {
                    "pick": {
                        "type": "choice",
                        "choice": winner,
                        "confidence": pick_probs[winner],
                        "probabilities": pick_probs,
                    },
                    **fits_answers,
                },
                "usage": _usage(50),
            }
        else:
            answers = {
                name: {"type": "noul", "noul": {"s0": 0.9, "s1": 0.6, "s2": 0.2}[name]}
                for name in questions
                if name.startswith("s")
            }
            return 200, {"model": "jev-test", "answers": answers, "usage": _usage(100)}

    routed, client = _routed_client(route)
    patched_tool_client(client, module=MODULE)

    data = await _call(SKILLS_3, switch_threshold=2)

    assert data["selected"] == []
    assert data["detail"]["overall_verdict"] == "none_fit"


async def test_auto_two_step_low_margin(patched_tool_client):
    """step2 margin < margin_threshold → 冠亚军 review。"""
    def route(body):
        questions = body["questions"]
        if "pick" in questions:
            pick_probs = {}
            fits_answers = {}
            for k in questions["pick"]["criteria"]:
                if k == "__no_skill_needed__":
                    pick_probs[k] = 0.0
                else:
                    pick_probs[k] = {"s0": 0.45, "s1": 0.4, "s2": 0.15}.get(k, 0.1)
                if k != "__no_skill_needed__":
                    fits_answers[f"fits::{k}"] = {"type": "noul", "noul": 0.8}
            winner = max(pick_probs, key=lambda k: pick_probs[k])
            return 200, {
                "model": "jev-test",
                "answers": {
                    "pick": {
                        "type": "choice",
                        "choice": winner,
                        "confidence": pick_probs[winner],
                        "probabilities": pick_probs,
                    },
                    **fits_answers,
                },
                "usage": _usage(50),
            }
        else:
            answers = {
                name: {"type": "noul", "noul": {"s0": 0.9, "s1": 0.6, "s2": 0.2}[name]}
                for name in questions
                if name.startswith("s")
            }
            return 200, {"model": "jev-test", "answers": answers, "usage": _usage(100)}

    routed, client = _routed_client(route)
    patched_tool_client(client, module=MODULE)

    data = await _call(SKILLS_3, switch_threshold=2)

    assert data["selected"] == []
    assert data["detail"]["overall_verdict"] == "low_margin"
    assert [r["name"] for r in data["reviewed"]] == ["ppt-architect", "pdf"]
    assert all(r["reason"] == "low_margin" for r in data["reviewed"])


async def test_auto_two_step_batch_failure_isolated(patched_tool_client):
    """step1 首批失败 → 该批技能 invalid，其余有效技能继续到 step2。"""
    def route(body):
        questions = body["questions"]
        if "pick" in questions:
            pick_probs = {}
            fits_answers = {}
            for k in questions["pick"]["criteria"]:
                if k == "__no_skill_needed__":
                    pick_probs[k] = 0.0
                else:
                    pick_probs[k] = {"s1": 0.7, "s2": 0.2}.get(k, 0.1)
                if k != "__no_skill_needed__":
                    fits_answers[f"fits::{k}"] = {"type": "noul", "noul": 0.8}
            winner = max(pick_probs, key=lambda k: pick_probs[k])
            return 200, {
                "model": "jev-test",
                "answers": {
                    "pick": {
                        "type": "choice",
                        "choice": winner,
                        "confidence": pick_probs[winner],
                        "probabilities": pick_probs,
                    },
                    **fits_answers,
                },
                "usage": _usage(50),
            }
        # 首批（s0）失败
        if "s0" in questions:
            return 502, {"error": "upstream"}
        # 其他批正常
        answers = {
            name: {"type": "noul", "noul": 0.9}
            for name in questions
            if name.startswith("s")
        }
        return 200, {"model": "jev-test", "answers": answers, "usage": _usage(100)}

    routed, client = _routed_client(route, max_retries=1)
    patched_tool_client(client, module=MODULE)

    data = await _call(SKILLS_3, switch_threshold=2, max_skills_per_batch=1)

    # s0 所在批失败 → invalid；s1/s2 有效，best_recall=0.9 ≥ exists_threshold → 继续 step2
    assert data["steps"] == 2
    assert any(i["name"] == "ppt-architect" for i in data["invalid"])
    assert data["detail"]["overall_verdict"] == "auto_select"


async def test_auto_switch_threshold_boundary(mock_api, make_client, patched_tool_client):
    """len(skills) == switch_threshold → 单步。"""
    mock_api.push(200, _auto_pick_body({"s0": 0.9, "s1": 0.1, "s2": 0.1}))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(SKILLS_3, switch_threshold=3)  # 3 skills == 3

    assert data["mode"] == "auto"
    assert data["steps"] == 1  # 单步
    assert data["batches"] == 1


async def test_auto_single_step_truncated_demoted_to_review(mock_api, make_client, patched_tool_client):
    """description 截断 → review，不自动 select。"""
    skills = [{"name": "long", "description": "x" * 100}] + SKILLS_3[1:]
    mock_api.push(200, _auto_pick_body({"s0": 0.8, "s1": 0.15, "s2": 0.05}))
    patched_tool_client(make_client(), module=MODULE)

    data = await _call(skills, description_max_chars=10)

    assert data["selected"] == []
    assert data["reviewed"][0]["name"] == "long"
    assert data["reviewed"][0]["reason"] == "truncated"


async def test_auto_single_step_over_255_skills_rejected(make_client, patched_tool_client):
    """auto 单步超过 MAX_CHOICE_OPTIONS → 报错。"""
    from fastmcp import Client
    from fastmcp.exceptions import ToolError as ClientToolError

    from jev_harness_tool.server import create_server

    patched_tool_client(make_client(), module=MODULE)
    skills = [{"name": f"skill-{i}", "description": "d"} for i in range(256)]

    async with Client(create_server()) as c:
        with pytest.raises(ClientToolError, match="255"):
            await c.call_tool(
                "skill_search", {"query": "q", "skills": skills, "switch_threshold": 256}
            )


# ---------- 空候选 & 重试/错误 ----------


async def test_empty_skills_no_request(mock_api, make_client, patched_tool_client):
    patched_tool_client(make_client(), module=MODULE)

    data = await _call([])

    assert data["selected"] == [] and data["reviewed"] == [] and data["skipped"] == []
    assert data["none_probability"] is None
    assert data["batches"] == 0
    assert data["usage"]["input_tokens"] == 0
    assert len(mock_api.requests) == 0  # 不发请求


async def test_retry_502_then_success(mock_api, make_client, patched_tool_client):
    mock_api.push(502, {"error": "upstream"}).push(200, _auto_pick_body({"s0": 0.9}))
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
async def test_real_skill_search_auto_ppt():
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(REAL_SKILLS, query=REAL_QUERY)

    assert data["invalid"] == []
    assert data["mode"] == "auto"
    assert data["steps"] == 1  # 10 skills ≤ 50
    assert data["none_probability"] is not None
    contenders = {s["name"] for s in data["selected"] + data["reviewed"]}
    assert contenders & {"ppt-architect", "html-ppt"}
    assert data["usage"]["input_tokens"] > 0
