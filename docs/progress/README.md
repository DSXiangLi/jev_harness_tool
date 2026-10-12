# 迭代记录（progress）

## 最近更新

- **2026-10-13** — [skill_search v2.0（单一 auto 模式）+ 移除 route_decide](1013_skill_search_v2_remove_route_decide.md)：skill_search 收敛为 auto（单步 choice 内嵌 no_skill_needed / 两步 noul 召回 + choice 精排 + fits noul），文档全量同步；删除 route_decide（routing 仅保留 effort_route），工具数 9 → 8。mock 198 全绿。
- **2026-10-12** — [reference_verify v1.1：引入 context 补充证据](1012_reference_verify_context.md)：对照官方 citation cookbook 引入 passage 所在段落上下文（可选 `context` 字段 + `context_max_chars`），instructions/criteria 措辞升级为结合 context 判定；真实 API 验证"context 含 OPTIONAL 宽限句"用例由 review 翻转为 contradicted/block。mock 220 全绿。
- **2026-10-08** — [SproutHarness 真实场景测试](1008_sprout_harness_real_scenarios.md)：用 lixiang 用户的真实 MEMORY.md 索引、历史会话脱敏 query、SproutHarness 真实技能目录和代码段落，为 9 个工具各构造 3 个真实场景测试（共 27 个），全部通过。文档含 jev 每次预测的真实概率、verdict 和 usage。
- **2026-09-29** — [M1–M4：初始实现（9 工具全量上线）](0929_m1_m4_initial_implementation.md)：core + 9 个决策工具完成，217 mock + 14 real 测试全绿；含 post_readymade 双契约拆分与 get_shared_client 循环感知单例两处关键修复。

## 按日期索引

| 日期 | 条目 | 摘要 |
|---|---|---|
| 2026-10-13 | [1013_skill_search_v2_remove_route_decide](1013_skill_search_v2_remove_route_decide.md) | skill_search v2.0 单一 auto 模式 + 文档同步；移除 route_decide（保留 effort_route） |
| 2026-10-12 | [1012_reference_verify_context](1012_reference_verify_context.md) | reference_verify v1.1：context 补充证据 + 措辞升级，真实 API 验证 verdict 翻转 |
| 2026-10-08 | [1008_sprout_harness_real_scenarios](1008_sprout_harness_real_scenarios.md) | SproutHarness 真实场景测试：9 工具 × 3 场景 = 27 个 real 测试，全部通过 |
| 2026-09-29 | [0929_m1_m4_initial_implementation](0929_m1_m4_initial_implementation.md) | M1–M4 初始实现：core + 9 工具，231 用例全绿 |
