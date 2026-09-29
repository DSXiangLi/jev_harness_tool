# 2026-09-29 M1–M4：初始实现（9 工具全量上线）

## 范围

按 `docs/design/jev_mcp_design.md` §9 的里程碑完成 M1–M4（M5 SproutHarness 接入在其自有仓库，不在本次范围）：

- **M1**：core 层（client/config/questions/threshold/tokens）+ `jev_decide` 通用决策原语 + 真实 API 连通性验证。
- **M2**：`memory_gate`（记忆加载门控）、`skill_search`（技能检索，multi/choice 双模式）。
- **M3**：`reference_verify`（内联引用验证，token 预算分批并发）。
- **M4**：`route_decide` / `effort_route`（routing）、`result_verify`、`tool_risk_gate`、`context_filter`。

## 测试规模

共 231 个用例，全绿：

- **217 mock**（默认 `pytest -m "not real"`）：本地可编排 HTTP mock（`MockJevAPI`）+ 真实 `JevClient` 端到端，覆盖重试序列、歧义网络错误不重发、envelope 双层校验、分批合并、阈值边界，不烧 credits。
- **14 real**（`pytest -m real`，需 `.env` 中 `JV_API_KEY`）：每个工具 ≥1 条真实 API 用例验证语义符合预期。

## 关键修复（相对初版设计的两处重要修正）

1. **`post_readymade` 双契约拆分**：实测发现 jev 现成端点（`/api/v1/agent/risk`、`/api/v1/context/filter`）的 200 响应是扁平的 `{action, risk, ...}`，**没有 `answers` 字段，也可能没有 `model`**，与 `/decide` 的 envelope 契约不同。core/client 因此拆分为 `_parse_envelope`（/decide：answers 必填、usage 必填）与 `_parse_readymade`（现成端点：只要求合法 JSON 对象，usage 存在才校验）；新增 `JevProtocolError`（`JevUpstreamError` 子类）表达"200 但契约畸形"，工具层可单独捕获并 fail-closed 为 `invalid_response`，与重试耗尽的真正上游故障区分开。
2. **`get_shared_client` 循环感知单例**：原各工具模块各自缓存 `JevClient`，而 `httpx.AsyncClient` 绑定创建时的事件循环，pytest-asyncio 每用例一个新循环会复用到已关闭循环上的 client（"Event loop is closed"）。core/client 新增 `get_shared_client()` / `reset_shared_client()`：检测到当前运行循环与创建时不同则丢弃重建；各工具的 `get_client()` 统一委托该单例（测试仍通过 monkeypatch 工具模块的 `get_client` 注入 mock）。

## 各工具真实验证结论（real 用例）

- `jev_decide`：真实 /decide 调用返回合法 noul 概率与 usage。
- `route_decide`：「查函数调用链」正确路由到只读探索候选（routed/allow）。
- `effort_route`：「1+1等于几」选中最低档位 low，level_order 回显正确。
- `memory_gate`：依赖个人偏好的 query 判 needed（inject），自包含通用 query 判 not_needed（skip）。
- `skill_search`：PPT 类 query 在 multi 模式选中 ppt-architect、明显无关技能全部 skip；choice 模式存在性门控放行且胜出者在预期集合内。
- `reference_verify`：明确支持的 passage 判 supported/allow，明显无关的判 unsupported/review。
- `result_verify`：完整满足要求的产出判 passed（两个真实用例：passed / not passed 均验证）。
- `tool_risk_gate`：危险命令不被自动放行（block/review，risk > 0.5），安全只读操作 allow（risk < 0.3）。
- `context_filter`：单条路径过期 README 条目判 drop/truncate 且冗余/低相关信号正确；批量路径 3 条目相关项 keep、无关项 drop/truncate、无 invalid。
