# MCP 工具验收（9 工具，M1–M4）

验收日期：2026-09-29。2026-10-13 更新：skill_search 收敛为单一 auto 模式（详设 v2.0）；reference_verify 升级 v1.1（可选 context）；移除 `route_decide` 工具（routing 仅保留 `effort_route`），相应测试同步删除；新增 `input_llm_guardrail`（9 hazard noul + true/false criteria，v3 措辞精简）。当前测试规模：237 mock（`pytest -m "not real"`）+ 37 real（`pytest -m real`），mock 全绿。
测试文件位于 `tests/`，与工具一一对应。

## P0：真实 API 语义（每工具 ≥1 real 用例通过）

| 工具 | 测试文件 | 真实验证结论 |
|---|---|---|
| `jev_decide` | tests/test_decide.py | 真实 /decide 调用返回合法 noul 概率与 usage（input_tokens > 0） |
| `effort_route` | tests/test_routing.py | 「1+1等于几」选中最低档 low，verdict=selected，level_order 回显正确 |
| `memory_gate` | tests/test_memory_gate.py | 依赖个人偏好的 query 判 needed（inject_full/inject_index）；自包含通用 query 判 not_needed / skip |
| `skill_search` | tests/test_skill_search.py | auto 单步：PPT query 选中预期 PPT 技能、明显无关技能全 skip、无 invalid；候选集覆盖盲区时 `__no_skill_needed__` 正确胜出（none_relevant，真实 API 6 场景 6/6，见详设 §7.1） |
| `reference_verify` | tests/test_reference_verify.py | 明确支持的 passage 判 supported/allow（relation=direct_support，support ≥ 0.7）；明显无关的判 unsupported/review。**v1.1（2026-10-12）**：可选 `context`（passage 所在段落上下文）进入 wire state、计入预算与截断 fail-closed；真实验证"passage 字面看似支持、context 含 OPTIONAL 宽限句"用例由 review 翻转为 contradicted/block |
| `result_verify` | tests/test_result_verify.py | 完整满足要求的产出判 passed（probability ≥ 0.8）；不完整产出判 review/failed（not passed） |
| `tool_risk_gate` | tests/test_tool_risk_gate.py | 危险命令绝不被自动放行（block/review，risk > 0.5）；安全只读操作 allow（risk < 0.3） |
| `context_filter` | tests/test_context_filter.py | 单条路径：过期 README 条目判 drop/truncate，冗余/低相关信号正确；批量路径：相关条目 keep、无关条目 drop/truncate、无 invalid |
| `input_llm_guardrail` | tests/test_input_llm_guardrail.py | **真实 API 未验收**（README 标 `未验证`）。mock 39 项全绿：block/pass/support/review 正常路径、9 hazard 触发真值表、优先级裁决、命名策略、截断封顶、重试纪律 |

## P0：错误纪律（断言请求数）

对应各测试文件中的 408/429/5xx 重试序列、401/402/403 不重试、歧义网络错误不重发用例（mock 端点按请求计数断言）：

- 401/402/403/400 分别映射 JevAuthError / JevPaymentError / JevValidationError，**不重试**（请求数 == 1）。
- 歧义网络错误（连接重置、TLS 失败、超时）映射 JevNetworkError，**绝不重发**（请求数 == 1，付费调用无幂等键）。
- 仅 408/429/5xx 按指数退避 + jitter 重试，耗尽后 JevUpstreamError（请求数 == max_retries + 1）。
- 批量工具（skill_search / reference_verify / context_filter 批量路径）中认证/付费/构造错误为整调用错误（抛 ToolError），歧义网络错误只 fail-closed 该批为 invalid_response，不影响其他批次。

## P0：fail-closed

- 缺失/畸形答案标 `invalid_response`，绝不伪装成"通过"或"否定"（各工具 mock 用例覆盖：noul 越界、choice 非 argmax、概率键不匹配、answers 缺失等）。
- 被截断的输入永不自动通过：effort_route 截断降级 review；memory_gate 截断封顶 inject_index；reference_verify 截断对永不 supported；context_filter 截断条目永不自动 keep；result_verify 截断永不 passed；tool_risk_gate 截断升档；input_llm_guardrail 截断封顶 pass→review（不封顶 block/support）。
- 现成端点 200 但契约畸形 → JevProtocolError → 工具层 verdict=invalid_response（context_filter / tool_risk_gate）。
- confidence 未知视为不满足任何阈值（context_filter 保守化、tool_risk_gate 升档）。
- input_llm_guardrail：单个 hazard noul 畸形仅该 hazard 置 None 跳过，不炸整体；全部 9 个 noul 畸形 → invalid_response。

## P1：工程约束

| 验收项 | 覆盖 |
|---|---|
| 阈值参数化与回显 | 所有工具阈值均为可选参数带默认值，响应回显 `thresholds`（各测试文件阈值边界与回显用例） |
| usage 透传 | decide 类工具回显完整 usage（input_tokens / cost_usd / credits_remaining_usd）；批量工具多批 usage 求和；现成端点 usage 缺失/畸形时置 None 不判 invalid |
| 批量分批合并 | skill_search / reference_verify / context_filter 按 64k/32k×0.85 token 预算装箱、多批并发、state 随每批重发、结果按输入顺序回填（wire 位置 key，外部 id 原样透传） |
| margin 判据 | effort_route / skill_search / reference_verify / context_filter(批量)：冠亚军 margin 不足降级 review，不自动裁决 |
| 优先级裁决 | input_llm_guardrail：多 hazard 同时触发按 support > block > review > pass 取最高；命名策略 strict/permissive 快捷设置双阈值，独立阈值参数可覆盖 |
| 空输入零请求 | skill_search 空 skills、reference_verify 空 pairs 直接返回空结果，不发起 HTTP 请求（断言 mock 请求数 == 0） |
