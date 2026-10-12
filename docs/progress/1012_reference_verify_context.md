# reference_verify v1.1：引入 context 补充证据 + 措辞升级

> 日期：2026-10-12
> 范围：`src/jev_harness_tool/tools/reference_verify.py`、`tests/test_reference_verify.py`、`docs/design/tools/reference_verify.md`（v1.0 → v1.1）、README、总体设计、验收文档
> 上游对比：typesafe 官方 cookbook《Double-checking citations》（`docs/research/` 对比结论见下）

## 背景：与官方 cookbook 的异同

官方实现的输入是「原始文档 + citation{claim, quote, section}」：先确定性字符串匹配找 quote（找不到 → `fabricated`，零模型成本），再把 quote 所在 section 全文交给一个 Choice 问题判 supports/contradicts/says_nothing，单阈值 confidence ≥ 0.8 决定 auto/review。

我方实现输入是调用方预切好的 (claim, passage) 对，已有官方没有的强化：support noul + relation choice 双信号交叉校验、margin 第二判据、partial_support 档、共享 state 批量 + token 预算分批并发、防注入声明、截断 fail-closed。官方最值得我们引入的是 **section 全文上下文**——其 `exp_required` 案例表明：quote 一字不差地存在于源文档，但所在 section 的上下文说它 OPTIONAL，仅看 quote 片段会漏判。

本轮按用户拍板方向引入该能力：`context` 字段 + 措辞升级。未引入官方的 fabricated 检测（quote 字符串匹配）——它是确定性逻辑但要求工具持有原始文档与 quote 概念，改变工具边界，留作后续候选。

## 改动内容

1. **新增可选 `pairs[].context`**：passage 所在段落的完整上下文。缺失/空串/非 str 按未提供处理（不标 invalid）；wire state 条件携带，不传 context 时请求体与 v1.0 字节级一致（向后兼容，有测试断言）。
2. **新增参数 `context_max_chars`**（默认 8000）：头部保留截断；`truncated` 语义扩展为 passage 或 context 任一截断即 true，沿用"截断对永不自动通过"的 fail-closed。
3. **措辞升级（单模板 + 条件语句，同批允许有/无 context 混合）**：
   - support noul：判断口径扩展为"passage 字面看似支持但 context 限定/排除/否定 → 不算有依据"；
   - relation criteria：`direct_support` 要求 context 中无限定/排除/否定；`contradicts` 明确纳入"context 限定否定"情形（如标注可选、排除适用情形）。
4. **预算**：context 计入 `state_tokens` 估算，推动分批的行为不变。

## 验证结果

- mock 全量：`pytest -m "not real"` 220 通过、40 deselected；新增 6 个 context 用例（wire 携带/兼容缺省/空值容忍/混合批/截断 fail-closed/预算分批）。
- 既有 real 用例：reference_verify 相关 4 个 real 测试全绿（instructions 措辞变更无回归）。
- 真实 API 对照验证（官方 `exp_required` 案例形态，RFC 7519 §4.1.4）：
  - claim「exp 处理不允许任何时钟偏差宽限」+ passage 截取到 `MUST be before` 句为止：
    - 无 context：`unsupported/review`（support=0.17，relation=direct_support 但 margin=0.25，信号含糊）；
    - 有 context（含 `Implementers MAY provide for some small leeway`）：**`contradicted/block`**（support=0.08，margin=0.98）——context 使 verdict 翻转且信号显著增强。
  - 成本：2 pairs 单次 /decide，1898 input tokens ≈ $0.0008。

## 遗留候选（本轮未做）

- **fabricated 检测**：quote 归一化子串匹配（官方第一阶段），需工具持有原始文档/quote 概念，涉及工具边界调整；
- **fuzzy quote 匹配**：官方亦承认 exact match 对被截断/轻微改写的 quote 会误报 fabricated。
