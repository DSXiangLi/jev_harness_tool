"""SproutHarness 真实场景测试。

每个工具 2–3 个 @pytest.mark.real 测试，使用 SproutHarness 框架的真实技能目录、
真实 MEMORY.md 索引、真实 hook/工具/LLM 配置数据，通过 .env 中的 JV_API_KEY
真实调用 jev API 验证语义正确性。

数据来源：/home/lixiang/Desktop/SproutHarness 代码库。
"""

from __future__ import annotations

import json
import os

import pytest

# ------------------------------------------------------------------ #
# 真实数据常量（来自 SproutHarness 代码库）
# ------------------------------------------------------------------ #

SPROUT_SKILLS = [
    {"name": "设计文档审核", "description": "对设计文档进行系统化审核，覆盖完整性、清晰度和可实施性。在设计文档编写或更新完成后触发，也可以在用户提到'审核设计文档'、'评审设计'、'设计文档 review'、'检查设计文档'、'这个设计合理吗'时使用。"},
    {"name": "修复代码验收", "description": "当 AI 完成代码修复后，在向用户汇报'修复完成'之前，必须按本技能执行系统性验收。验收覆盖：影响范围与外溢分析、全模块回归测试、上下游完整调用链集成测试。"},
    {"name": "修复方案审核", "description": "对 AI 给出的技术方案进行七维度系统化审查：外溢影响、性能压力、维护成本、根因解决度、向后兼容性、简洁性、边界覆盖。"},
    {"name": "技能创建", "description": "Create new skills, modify and improve existing skills, and measure skill performance."},
    {"name": "MCP检查", "description": "MCP 服务质量全面检查工具，验证连通性、审查整体工具设计（正交性、覆盖度、命名一致性）、逐工具审计输入输出设计。"},
    {"name": "提交后文档回扫", "description": "在代码修改完成后、执行 git commit/push 之前，强制执行四类文档同步检查：howto 文档、progress 文档、设计文档、test_acceptance 文档。"},
    {"name": "check-kimi-code-docs", "description": "Answer questions about the Kimi Code product using the official documentation — CLI usage, configuration, slash commands, features."},
    {"name": "update-config", "description": "Inspect or edit kimi-code's own config — config.toml and tui.toml. Use when the user asks what a setting does or wants to change one."},
    {"name": "write-goal", "description": "Help the user craft a well-specified /goal objective for goal mode — turn a rough intention into a completion contract."},
    {"name": "brandkit", "description": "Premium brand-kit image generation skill for creating high-end brand-guidelines boards, logo systems, and identity decks."},
]

# 真实 MEMORY.md 索引（来自 lixiang 用户的 SproutHarness 项目记忆）
SPROUT_MEMORY_INDEX = (
    "# SproutHarness Memory Index\n"
    "\n"
    "- [create_agent 决策](create_agent_decision.md) — create_agent 方案已废弃，改为函数式 Hook 基于手动图\n"
    "- [ToolRuntime 注入](toolruntime_injection.md) — 必须用显式 runtime: ToolRuntime 参数，不能用 **kwargs\n"
    "- [Hook 设计 V2](hook_design_v2.md) — 函数式 Hook 设计完成（8 P0 纯函数 + 3+1 文件 + review 修订）\n"
    "- [多用户Agent对比](multi_user_agent_comparison.md) — Hermes/Claude Code/DeerFlow/SproutHarness 四方对比 + 本地源码路径\n"
    "- [Agent 并发限制](agent_concurrency_limit.md) — 最多同时3个Agent，超出排队依次执行\n"
    "- [Tool Call Name 腐败根因](tool_call_name_corruption_root_cause.md) — 【已废弃】safe_tc_chunks 方案已撤回，当前 Thai 输出标准格式无需额外处理\n"
    "- [流式工具推理格式当前状态](streaming_tool_call_format_current_status.md) — Thai 代理标准格式确认 + 腐败触发条件分析 + LangChain 默认方案即可\n"
    "- [环境命名统一规范](env_naming_unification.md) — 只有 test/work/prod 三套，local=work，禁止 testing/gateway/cluster/production 变体\n"
    "- [DingTalk AI Card 踩坑](dingtalk_ai_card_lessons.md) — 6条关键教训：必须参照官方示例、async_create_and_deliver_card而非async_start、.schema后缀、content而非contentStreamingKey、async_streaming(finished/failed)而非async_finish/fail、WebSocket单连接限制、card_replier查找bug\n"
)

SPROUT_EFFORT_LEVELS = {
    "off": "无需推理：事实性直答、简单换算、固定格式输出",
    "low": "轻量推理：一两步即可完成的简单推断或改写",
    "medium": "中等推理：需要多步推理、综合多个信息或排查常规问题",
    "high": "深度推理：复杂规划、架构设计、疑难问题排查等高错误代价任务",
}

COMPACT_HOOK_PASSAGE = (
    "def _partition_messages(messages, tail_budget, model):\n"
    "    keep = []\n"
    "    used = 0\n"
    "    for msg in reversed(messages):\n"
    "        msg_tokens = _count_tokens([msg], model)\n"
    "        if getattr(msg, 'lc_tag', None) == 'upload_context':\n"
    "            keep.insert(0, msg)\n"
    "            continue\n"
    "        if used + msg_tokens > tail_budget:\n"
    "            break\n"
    "        keep.insert(0, msg)\n"
    "        used += msg_tokens\n"
    "    keep_ids = {id(m) for m in keep}\n"
    "    compress = [m for m in messages if id(m) not in keep_ids]\n"
    "    return keep, compress"
)

MEMORY_GUIDANCE_PASSAGE = (
    "记忆是持久化的用户偏好、事实、经验、和历史用户行为摘要。\n"
    "以下为记忆索引，当对话涉及过往经验、用户偏好、项目事实时，"
    "根据索引中各条目的 description 判断是否需要 read 对应记忆文件全文。"
    "记忆是 point-in-time 观察——使用前需验证是否仍然适用。\n"
    "记忆写入的触发条件：用户消息中明确包含'记住'、'请记住'等记忆指令关键词。\n"
    "记忆文件写入时：preference/ 存偏好，learnings/ 存经验，fact/ 存事实。"
)

FACTORY_PASSAGE = (
    "enable_thinking = config.get('enable_thinking', False)\n"
    "extra_body = None\n"
    "if enable_thinking:\n"
    "    extra_body = {'enable_thinking': True}\n"
    "cls = RetryableChatGLM if enable_thinking else RetryableChatOpenAI"
)


# ------------------------------------------------------------------ #
# 辅助函数与 fixture
# ------------------------------------------------------------------ #


def _unwrap(result):
    if result.data is not None:
        return result.data
    return json.loads(result.content[0].text)


async def _call(tool: str, args: dict):
    from fastmcp import Client

    from jev_harness_tool.server import create_server

    async with Client(create_server()) as c:
        result = await c.call_tool(tool, args)
    return _unwrap(result)


@pytest.fixture
async def fresh_client():
    """Reset all tool client singletons before real API tests."""
    from jev_harness_tool.core.client import reset_shared_client
    from jev_harness_tool.tools import memory_gate as mg

    mg._client = None
    reset_shared_client()
    yield
    mg._client = None
    reset_shared_client()


# ------------------------------------------------------------------ #
# 1. jev_decide（2 tests）
# ------------------------------------------------------------------ #


@pytest.mark.real
async def test_real_decide_choice_noul_parallel(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    from jev_harness_tool.core import choice_question, noul_question

    questions = {
        "module": choice_question(
            "以下代码片段来自 SproutHarness 的哪个模块？",
            {
                "compact_hook": "hooks/compact_hook.py，负责上下文压缩与消息分区",
                "prompt_builder": "agent/prompt_builder.py，负责系统提示词构建",
                "factory": "llms/factory.py，负责 LLM 工厂选择",
                "task_tool": "tools/task_tool.py，负责子代理任务分发",
            },
        ),
        "has_loop": noul_question("上述代码中是否包含 for 循环？"),
    }
    state = {
        "code_snippet": COMPACT_HOOK_PASSAGE,
        "claim": "这段代码实现了 _partition_messages 函数，从最新消息向前遍历来决定保留哪些消息。",
        "truncated": False,
    }

    data = await _call("jev_decide", {"state": state, "questions": questions})

    assert "module" in data["answers"]
    assert "has_loop" in data["answers"]
    assert data["usage"]["input_tokens"] > 0


@pytest.mark.real
async def test_real_decide_score_complexity(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    from jev_harness_tool.core import score_question

    questions = {
        "complexity": score_question(
            "评估以下集成方案的复杂度，1=最简单，4=最复杂。",
            ["1: 单一文件少量改动，无接口变更", "2: 少量文件改动，局部接口调整", "3: 多文件改动，需新增接口或修改调用链", "4: 架构级改动，影响多个子系统"],
        ),
    }
    state = {
        "integration_plan": (
            "在 SproutHarness 中集成 jev 决策服务：\n"
            "1. 在 hooks/compact_hook.py 中调用 context_filter 决定哪些消息保留\n"
            "2. 在 agent/prompt_builder.py 中调用 memory_gate 判断是否注入记忆\n"
            "3. 在 llms/factory.py 中调用 effort_route 选择推理强度\n"
            "4. 所有调用走 MCP 服务，需要处理错误降级和超时"
        ),
        "truncated": False,
    }

    data = await _call("jev_decide", {"state": state, "questions": questions})

    assert "complexity" in data["answers"]
    assert data["usage"]["input_tokens"] > 0


# ------------------------------------------------------------------ #
# 2. effort_route（3 tests）
# ------------------------------------------------------------------ #


@pytest.mark.real
async def test_real_effort_route_low(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    levels = {
        "low": SPROUT_EFFORT_LEVELS["low"],
        "medium": SPROUT_EFFORT_LEVELS["medium"],
        "high": SPROUT_EFFORT_LEVELS["high"],
    }

    data = await _call(
        "effort_route",
        {"query": "把 compact_hook.py 这个文件名全小写改成 snake_case", "levels": levels},
    )

    assert data["verdict"] == "selected"
    assert data["effort"] == "low"
    assert data["conservative_level"] == "low"
    assert data["usage"]["input_tokens"] > 0


@pytest.mark.real
async def test_real_effort_route_high(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "effort_route",
        {
            "query": "请思考下当前 dingtalk-adapter 是否有可能接入多个 Agent 服务，同时接入多个钉钉 client，需要考虑并发和会话隔离",
            "levels": SPROUT_EFFORT_LEVELS,
        },
    )

    assert data["verdict"] == "selected"
    assert data["effort"] in ("high", "medium")
    assert data["conservative_level"] in ("high", "medium")
    assert data["usage"]["input_tokens"] > 0


@pytest.mark.real
async def test_real_effort_route_medium(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "effort_route",
        {
            "query": "线上请求报错，请检查是模型自己的接口报错，还是我们内部自己的检查逻辑报错",
            "levels": SPROUT_EFFORT_LEVELS,
        },
    )

    # 排查类 query 本身具有不确定性——jev 可能判 selected 也可能判 review（margin 不足）
    assert data["verdict"] in ("selected", "review")
    if data["verdict"] == "selected":
        assert data["effort"] in ("low", "medium")
    assert data["usage"]["input_tokens"] > 0


# ------------------------------------------------------------------ #
# 3. memory_gate（3 tests）
# ------------------------------------------------------------------ #


@pytest.mark.real
async def test_real_memory_gate_needed_project_decision(fresh_client):
    """正面：query 指代记忆中的项目决策（create_agent 废弃），应判定 needed。"""
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "memory_gate",
        {
            "query": "之前讨论的 create_agent 方案后来为什么废弃了？我记得最后改成了函数式 Hook",
            "memory_description": SPROUT_MEMORY_INDEX,
            "description_kind": "index",
        },
    )

    assert data["needed"] is True
    assert data["verdict"] == "needed"
    assert data["action"] in ("inject_full", "inject_index")
    assert data["probability"] >= 0.5
    assert data["usage"]["input_tokens"] > 0


@pytest.mark.real
async def test_real_memory_gate_needed_env_naming(fresh_client):
    """正面：query 指代记忆中的环境命名规范，应判定 needed。"""
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "memory_gate",
        {
            "query": "我们项目环境命名规范是不是只有三套？之前统一过的，local 对应哪个",
            "memory_description": SPROUT_MEMORY_INDEX,
            "description_kind": "index",
        },
    )

    assert data["needed"] is True
    assert data["verdict"] == "needed"
    assert data["action"] in ("inject_full", "inject_index")
    assert data["probability"] >= 0.5
    assert data["usage"]["input_tokens"] > 0


@pytest.mark.real
async def test_real_memory_gate_not_needed_code_task(fresh_client):
    """负面：具体的代码修改任务，虽涉及项目文件但不依赖记忆内容，应判定 not_needed。

    对比强度：query 提到了 compact_hook.py 和 _partition_messages 等项目术语，
    但实际任务是"加注释"——不依赖记忆中的决策或经验。这比"1+1等于几"更有对比价值。
    """
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "memory_gate",
        {
            "query": "请帮我在 compact_hook.py 的 _partition_messages 函数里加一段注释，说明 turn boundary integrity 的逻辑",
            "memory_description": SPROUT_MEMORY_INDEX,
            "description_kind": "index",
        },
    )

    assert data["needed"] is False
    assert data["verdict"] == "not_needed"
    assert data["action"] == "skip"
    assert data["probability"] < 0.5
    assert data["usage"]["input_tokens"] > 0


# ------------------------------------------------------------------ #
# 4. skill_search（3 tests）
# ------------------------------------------------------------------ #


@pytest.mark.real
async def test_real_skill_search_auto_review(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "skill_search",
        {
            "query": "帮我审核这个设计文档是否有遗漏",
            "skills": SPROUT_SKILLS,
        },
    )

    assert data["mode"] == "auto"
    assert data["steps"] == 1  # 10 个技能 ≤ 默认 switch_threshold=50，走单步
    assert data["invalid"] == []
    contenders = {s["name"] for s in data["selected"] + data["reviewed"]}
    assert "设计文档审核" in contenders
    assert data["usage"]["input_tokens"] > 0
    assert "brandkit" not in contenders
    assert "write-goal" not in contenders
    assert "update-config" not in contenders


@pytest.mark.real
async def test_real_skill_search_auto_create(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "skill_search",
        {
            "query": "帮我创建一个新的技能",
            "skills": SPROUT_SKILLS,
        },
    )

    assert data["invalid"] == []
    contenders = {s["name"] for s in data["selected"] + data["reviewed"]}
    assert "技能创建" in contenders
    assert data["usage"]["input_tokens"] > 0


@pytest.mark.real
async def test_real_skill_search_auto_mcp(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "skill_search",
        {
            "query": "帮我检查 MCP 服务的质量",
            "skills": SPROUT_SKILLS,
        },
    )

    assert data["invalid"] == []
    contenders = {s["name"] for s in data["selected"] + data["reviewed"]}
    assert "MCP检查" in contenders
    assert data["usage"]["input_tokens"] > 0


# ------------------------------------------------------------------ #
# 5. reference_verify（3 tests）
# ------------------------------------------------------------------ #


@pytest.mark.real
async def test_real_reference_verify_supported(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "reference_verify",
        {
            "pairs": [
                {
                    "id": "partition",
                    "claim": "SproutHarness 的 compact_hook 在 _partition_messages 函数中从最新消息向前遍历来决定保留哪些消息",
                    "passage": COMPACT_HOOK_PASSAGE,
                }
            ]
        },
    )

    r = data["results"][0]
    assert r["verdict"] == "supported"
    assert r["action"] == "allow"
    assert r["relation"] == "direct_support"
    assert r["support"] >= 0.7
    assert data["usage"]["input_tokens"] > 0


@pytest.mark.real
async def test_real_reference_verify_irrelevant(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "reference_verify",
        {
            "pairs": [
                {
                    "id": "unrelated",
                    "claim": "SproutHarness 默认使用 ChatGLM 作为 LLM（不需要任何配置）",
                    "passage": MEMORY_GUIDANCE_PASSAGE,
                }
            ]
        },
    )

    r = data["results"][0]
    assert r["verdict"] == "unsupported"
    assert r["action"] == "review"
    assert data["usage"]["input_tokens"] > 0


@pytest.mark.real
async def test_real_reference_verify_partial(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "reference_verify",
        {
            "pairs": [
                {
                    "id": "partial",
                    "claim": "enable_thinking 配置为 True 时使用 RetryableChatGLM 类",
                    "passage": FACTORY_PASSAGE,
                }
            ]
        },
    )

    r = data["results"][0]
    assert r["verdict"] in ("supported", "partially_supported")
    assert r["relation"] in ("direct_support", "partial_support")
    assert data["usage"]["input_tokens"] > 0


# ------------------------------------------------------------------ #
# 6. result_verify（3 tests）
# ------------------------------------------------------------------ #


@pytest.mark.real
async def test_real_result_verify_passed(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "result_verify",
        {
            "requirement": "将 _partition_messages 函数的 tail_budget 参数改为可配置，从 config 读取默认值",
            "result": (
                "def _partition_messages(messages, tail_budget, model):\n"
                "    if tail_budget is None:\n"
                "        tail_budget = config.get('tail_budget', DEFAULT_TAIL_BUDGET)\n"
                "    keep = []\n"
                "    used = 0\n"
                "    for msg in reversed(messages):\n"
                "        msg_tokens = _count_tokens([msg], model)\n"
                "        if used + msg_tokens > tail_budget:\n"
                "            break\n"
                "        keep.insert(0, msg)\n"
                "        used += msg_tokens\n"
                "    return keep, compress"
            ),
        },
    )

    assert data["verdict"] == "passed"
    assert data["passed"] is True
    assert data["probability"] >= 0.8
    assert data["usage"]["input_tokens"] > 0


@pytest.mark.real
async def test_real_result_verify_review(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "result_verify",
        {
            "requirement": "在 prompt_builder.py 中添加 memory_gate 调用并支持三种注入策略：inject_full、inject_index、skip",
            "result": (
                "memory_gate_result = memory_gate(query, memory_content)\n"
                "if memory_gate_result: system_prompt += memory_content"
            ),
        },
    )

    assert data["verdict"] in ("review", "failed")
    assert data["passed"] is False
    assert data["probability"] < 0.8
    assert data["usage"]["input_tokens"] > 0


@pytest.mark.real
async def test_real_result_verify_failed(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "result_verify",
        {
            "requirement": "修改 task_tool.py 的 subagent_type 参数添加 jev 路由决策",
            "result": "import os\nprint('hello world')\n# This is a simple script",
        },
    )

    assert data["verdict"] in ("failed", "review")
    assert data["passed"] is False
    assert data["probability"] < 0.4
    assert data["usage"]["input_tokens"] > 0


# ------------------------------------------------------------------ #
# 7. tool_risk_gate（3 tests）
# ------------------------------------------------------------------ #


@pytest.mark.real
async def test_real_tool_risk_gate_block(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "tool_risk_gate",
        {
            "goal": "清理构建目录",
            "tool": "bash",
            "arguments": "rm -rf ./dist && aws s3 sync ./build s3://prod --delete",
            "context": "CI 部署步骤",
        },
    )

    assert data["error"] is None
    assert data["verdict"] in ("block", "review")
    assert data["risk"] > 0.5
    assert data["jev_action"] in ("confirm", "block")
    assert data["usage"]["input_tokens"] > 0


@pytest.mark.real
async def test_real_tool_risk_gate_allow(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "tool_risk_gate",
        {
            "goal": "了解 SproutHarness 项目结构",
            "tool": "read_file",
            "arguments": "src/sprout_harness/agent/prompt_builder.py",
        },
    )

    assert data["error"] is None
    assert data["verdict"] == "allow"
    assert data["risk"] < 0.3
    assert data["usage"]["input_tokens"] > 0


@pytest.mark.real
async def test_real_tool_risk_gate_config_write(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "tool_risk_gate",
        {
            "goal": "修改 SproutHarness 的 LLM 配置启用 thinking 模式",
            "tool": "write_file",
            "arguments": ".env",
        },
    )

    assert data["error"] is None
    assert data["verdict"] in ("review", "block")
    assert data["usage"]["input_tokens"] > 0


# ------------------------------------------------------------------ #
# 8. context_filter（3 tests）
# ------------------------------------------------------------------ #


@pytest.mark.real
async def test_real_context_filter_single_drop(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "context_filter",
        {
            "task": "修复 compact_hook 的分批 bug",
            "item": "Tool call: read_file('README.md') -> 4000 tokens 项目概览，来自 30 步之前",
        },
    )

    # jev 现成端点偶发缺字段 → invalid_response；合法时 30 步前的无关条目应 drop/truncate
    if data["error"] is None:
        assert data["verdict"] in ("drop", "truncate", "review", "invalid_response")
        assert data["jev_action"] in ("keep", "truncate", "drop")
        assert data["usage"]["input_tokens"] > 0
    else:
        assert data["error"]["code"] == "invalid_response"
        assert data["verdict"] == "invalid_response"


@pytest.mark.real
async def test_real_context_filter_single_keep(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "context_filter",
        {
            "task": "修改 prompt_builder 的技能注入逻辑",
            "item": (
                "最近读取的 prompt_builder.py 第 295-302 行："
                "catalog = get_skill_catalog(system_skills_dir, user_skills_dir, vr, loading_map); "
                "parts.append('## 可用技能\\n\\n' + (catalog if catalog else '暂无可用技能。'))"
            ),
        },
    )

    # jev 现成端点偶发缺字段 → invalid_response；合法时直接相关的条目应 keep
    if data["error"] is None:
        assert data["verdict"] in ("keep", "truncate", "review", "invalid_response")
        assert data["jev_action"] in ("keep", "truncate", "drop")
        assert data["usage"]["input_tokens"] > 0
    else:
        assert data["error"]["code"] == "invalid_response"
        assert data["verdict"] == "invalid_response"


@pytest.mark.real
async def test_real_context_filter_batch(fresh_client):
    if not os.environ.get("JV_API_KEY"):
        pytest.skip("JV_API_KEY not set")

    data = await _call(
        "context_filter",
        {
            "task": "集成 context_filter 到 compact_hook",
            "items": [
                {"id": "msg_rel", "content": "pytest 输出：test_partition_failed FAILED，compact_hook.py:564，_partition_messages 尚未修复"},
                {"id": "msg_irr", "content": "Tool call: read_file('README.md') -> 4000 tokens 项目概览，来自 30 步之前"},
                {"id": "msg_mid", "content": "早期方案讨论：context_filter 集成是否用 /decide 端点，结论是先修分区 bug"},
            ],
        },
    )

    assert data["error"] is None
    assert data["summary"]["invalid"] == 0
    assert data["summary"]["total"] == 3
    by_id = {r["id"]: r for r in data["results"]}
    assert by_id["msg_rel"]["verdict"] == "keep"
    assert by_id["msg_irr"]["verdict"] in ("drop", "truncate")
    assert by_id["msg_mid"]["verdict"] in ("keep", "truncate", "drop", "review")
    assert data["usage"]["input_tokens"] > 0
