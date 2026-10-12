# jev_harness_tool

基于 jev 决策模型 API 的通用 MCP 服务（Python + FastMCP）：为 harness 框架中各决策点（请求路由、记忆加载门控、技能检索、引用验证、结果验证、上下文去留、工具调用风险门控等）提供标准化的校准决策能力。问题设计（instructions 措辞、criteria 定义、choice/score/noul 组合）全部固化在服务端，调用方只传语义化输入；工具只返回概率、verdict 与建议动作（allow/review/block），不替调用方执行。

## 安装与配置

要求 Python >= 3.13。

```bash
pip install -e .          # 或 uv pip install -e .
pip install -e ".[dev]"   # 含 pytest / pytest-asyncio，用于跑测试
```

在项目根目录创建 `.env`（**禁止提交到版本库**）：

```bash
JV_API_KEY=<your jev api key>   # 必填
```

可选环境变量（默认值见 `src/jev_harness_tool/core/config.py`）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `JV_API_KEY` | （无，必填） | jev API key |
| `JEV_BASE_URL` | `https://jevtypesafeai.com` | jev API base URL |
| `JEV_MODEL` | `jev-latest` | 决策模型（生产建议钉版本，如 `jev-1.13.0`） |
| `JEV_TIMEOUT_S` | `60.0` | HTTP 超时（秒） |
| `JEV_MAX_RETRIES` | `3` | 对 408/429/5xx 的最大重试次数（指数退避 + jitter） |

## 运行

**stdio（默认，`server.py` 的 `main()`）：**

```bash
jev-harness-tool                    # pip 安装的 entry point
python -m jev_harness_tool.server   # 等价
```

**streamable_http（通过 FastMCP CLI 加载模块级 `mcp` 对象）：**

```bash
fastmcp run src/jev_harness_tool/server.py:mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

## 工具一览（8 个）

签名以 `src/jev_harness_tool/tools/*.py` 源码为准；所有阈值参数均为可选、带默认值，响应回显实际使用的 `thresholds` 与完整 `usage`（input_tokens / cost_usd）。

| 工具 | 用途 | 关键输入 | 关键输出 |
|---|---|---|---|
| `jev_decide` | 通用决策原语：state + 一组 choice/score/noul 问题 → 校准 answers（透传 /api/v1/decide） | `state`、`questions`、`model?` | `model`、`answers`、`usage` |
| `effort_route` | 推理投入决策：请求需要哪个 effort/thinking 档位（含 CDF 保守选档） | `query`、`levels{档位: 定义}`、`context?`、`effort_threshold=0.5`、`margin_threshold=0.15`、`safety_threshold=0.9` | `verdict`(selected/uncertain/review/invalid_response)、`effort`、`conservative_level`、`action`、`probabilities`、`level_order` |
| `memory_gate` | 记忆加载门控：当前 query 是否需要加载长期记忆（双阈值三档处置） | `query`、`memory_description`、`description_kind=summary/index`、`context?`、`threshold=0.5`、`high_threshold=0.8` | `needed`、`probability`、`verdict`(needed/not_needed/invalid_response)、`action`(inject_full/inject_index/skip/review) |
| `skill_search` | 技能检索决策：auto 单模式——skills ≤ switch_threshold 时单步 choice（criteria 内嵌 no_skill_needed 逃生舱），超过时两步（noul 召回 → choice 精排 + fits noul） | `query`、`skills[{name,description}]`、`threshold=0.3`、`max_results=5`、`margin_threshold=0.15`、`exists_threshold=0.3`、`fits_threshold=0.3`、`switch_threshold=50`、`recall_top_k=5` | `mode`(auto)、`steps`、`selected/reviewed/skipped/invalid[]`、`none_probability`、`batches`、`detail` |
| `reference_verify` | 内联引用验证：被引 passage（可附所在段落 context）是否支持 claim（每对 support noul + relation choice，自动分批并发） | `pairs[{id?,claim,passage,context?,source?}]`、`support_threshold=0.7`、`relation_margin=0.15`、`passage_max_chars=8000`、`context_max_chars=8000`、`max_pairs=50` | `results[]`（`verdict`: supported/partially_supported/unsupported/contradicted/review/invalid_response、`action`: allow/review/block、`invalid_reason`）、`summary` |
| `context_filter` | 上下文条目语义去留：单条走 jev 现成端点 context/filter；批量归约 /decide，keep/truncate/drop + 冗余覆盖 | `task`、`item` 或 `items[{id,content}]`（恰填其一）、`keep_min_relevance=moderate`、`confidence_min=0.4`、`action_prob_min=0.5`、`margin_min=0.2`、`redundancy_check=True` | 单条：`verdict`、`jev_action`、`relevance`、`redundant`、`confidence`；批量：`results[]`（概率分布 + margin + redundant）、`summary`、`batches` |
| `result_verify` | 结果门控：产出是否满足 requirement（noul + 可选 4 级完成度 score） | `requirement`、`result`、`threshold_pass=0.8`、`threshold_fail=0.4`、`include_completeness=True`、`result_max_chars=8000` | `verdict`(passed/review/failed/invalid_response)、`passed`、`probability`、`completeness` |
| `tool_risk_gate` | 工具调用前的语义风险门控（薄封装 jev 现成端点 agent/risk） | `goal`、`tool`、`arguments`、`context?`、`risk_allow_max=0.3`、`risk_block_min=0.7`、`confidence_min=0.4` | `verdict`(allow/review/block/invalid_response)、`jev_action`、`risk`、`categories`、`confidence` |

公共语义：畸形/缺失答案标 `invalid_response`，绝不伪装通过；被截断的输入永不自动通过；401/402/403 与歧义网络错误绝不重试；工具只建议不执行，降级策略由调用方决定。

## 测试

```bash
pytest -m "not real"   # 默认：mock jev API（本地 HTTP mock + 真实 client 端到端），198 个用例，不烧 credits
pytest -m real         # 真实 API（35 个用例），需 .env 中的 JV_API_KEY，会产生计费
```

## 文档导航

- 总体设计：`docs/design/jev_mcp_design.md`
- 工具详设（`docs/design/tools/`）：`memory_gate.md`、`skill_search.md`、`reference_verify.md`、`routing.md`、`result_verify.md`、`tool_risk_gate.md`、`context_filter.md`
- jev API 调研：`docs/research/jev-api-docs.md`
- 迭代记录：`docs/progress/`
- 验收标准：`docs/test_acceptance/`
