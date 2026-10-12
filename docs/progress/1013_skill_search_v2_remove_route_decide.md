# skill_search v2.0（单一 auto 模式）+ 移除 route_decide

> 日期：2026-10-13
> 范围：`src/jev_harness_tool/tools/{skill_search,routing}.py`、`tests/{test_skill_search,test_routing,test_real_scenarios}.py`、`docs/design/tools/{skill_search,routing}.md`、总体设计、README、验收文档

## 1. skill_search v2.0：收敛为单一 auto 模式

代码与详设 v2.0 于 2026-10-11/12 完成（实验依据见 `docs/design/tools/no_skill_experiment.html` 与详设「版本演进记录」），本轮补齐文档同步：

- **移除 multi/choice 两模式**，只保留 auto：
  - skills ≤ `switch_threshold`（默认 50）→ 单步：1 个 choice（pick），criteria 内嵌 `__no_skill_needed__` 逃生舱选项（同时看到 query + 全部技能描述，能识别"候选集覆盖盲区"，6 场景真实 API 6/6）；
  - skills > `switch_threshold` → 两步：step1 每技能独立 noul 召回（`exists_threshold` 存在性门控 + top-K）→ step2 choice 精排（内嵌 no_skill_needed）+ per-candidate fits noul（`fits_threshold`）。
- state 不再携带 catalog（技能 name+description 内联进 question/criteria），只含 task/context/query。
- 新增参数：`switch_threshold`、`recall_top_k`、`fits_threshold`；移除 `mode` 参数。
- 真实场景测试（`tests/test_real_scenarios.py`）3 个 skill_search 用例同步改为 auto 接口断言。

文档同步：README 工具一览、总体设计 §2/§3/§4/§7/§10、验收文档 skill_search 行均更新为 auto 语义；详设修正两处（reviewed 的 reason 清单去掉不存在的 `over_max_results`；两步路径分支 5 补充 winner fits 不足时的归属说明）。

## 2. 移除 route_decide（routing 仅保留 effort_route）

按用户拍板，删除子代理路由工具 `route_decide`，保留推理档位工具 `effort_route`：

- `src/jev_harness_tool/tools/routing.py`：删除 route_decide 工具及其专用构造（`ROUTE_INSTRUCTIONS` / `ROUTE_ESCAPE_CRITERIA` / `COMPLEXITY_*` / `route_questions` / `score_question` 依赖），模块只留 effort_route。
- `docs/design/tools/routing.md`：重写为 effort_route-only 详设（v1.1），route_decide 历史设计见 git 历史 v1.0。
- `tests/test_routing.py`：重写为 effort_route-only（含原 route 用例中可复用的纪律类用例已在 effort 路径覆盖）。
- `tests/test_real_scenarios.py`：删除 route_decide 3 个 real 用例与 route+effort 组合用例（组合调用设计点随 route_decide 一并移除），章节重编号为 8 个工具。
- 总体设计 / README / 验收文档：工具计数 9 → 8，移除 route_decide 行与相关机制描述（复杂度 score、no_route 分支等）。

## 3. 验证

- mock 全量：`pytest -m "not real"`（排除未提交的 llm_guardrail WIP）**198 通过**、35 deselected（real）。
- real 用例数随 test_real_scenarios.py 入库更新为 35（README 与验收文档已同步计数）。

## 4. 未纳入本次提交

- `src/jev_harness_tool/tools/llm_guardrail.py` + `tests/test_llm_guardrail.py`：新工具 WIP（缺 `docs/design/tools/llm_guardrail.md` 详设），按用户决定本次不提交。
