# skill_search —— 技能检索决策工具详细设计

> 版本：v2.0（单一 auto 模式，移除 multi/choice 模式，移除 gate noul，引入 `__no_skill_needed__` 选项）
> 日期：2026-10-12
> 上游文档：`docs/design/jev_mcp_design.md` §4.2、`docs/research/jev-api-docs.md`

## 版本演进记录

| 版本 | 日期 | 核心变更 |
|---|---|---|
| v0.2 | — | 总体设计初版：multi/choice 两模式，choice 含合成 none 选项 |
| v1.0 | 2026-09-28 | 取消合成 none，改为 choice + exists noul；token 预算驱动分批；position key；description 截断 |
| v1.1 | 2026-10-11 | 新增 auto 模式（noul 召回 + choice 精排 + 3 gate noul）；移除 catalog 出 state |
| v1.2 | 2026-10-11 | gate 从 3 noul 简化为 1 noul |
| **v2.0** | **2026-10-12** | **移除 multi/choice 模式，只保留 auto；移除 gate noul，改为 choice criteria 内嵌 `__no_skill_needed__` 选项；所有指令改为中文；改进 instructions 措辞（6/6 准确率）** |

### v2.0 关键决策的实验依据

详见 `docs/design/tools/no_skill_experiment.html`（真实 API A/B 测试）。

**实验 1：gate noul vs `__no_skill_needed__` 选项（5 场景对比）**

gate noul 只看 query 不看 候选技能集，无法识别"请求需要技能但 候选技能集 里没有"（Mastodon 场景）。`__no_skill_needed__` 选项放进 choice criteria，同时看到 query + 全部技能描述，能正确识别 候选技能集 覆盖盲区。

**实验 2：旧版 instructions vs 改进版 instructions（6 场景 A/B 测试，真实 API）**

| 场景 | 旧版 | 改进版 | 变化 |
|---|---|---|---|
| 明确命中（做PPT） | ✓ html-pptx 1.000 | ✓ html-pptx 0.990 | 保持 |
| 盲区：移除技能创建 | ✓ no_skill=0.640 | ✓ no_skill=**0.900** | 信心增强 |
| 盲区：移除MCP检查 | ✗ mcp-builder 0.720 | ✓ no_skill=**0.630** | **翻转** |
| 盲区：移除设计文档审核 | ✗ 提交后文档回扫 0.490 | ✓ no_skill=**0.730** | **翻转** |
| 纯文字够用 | ✓ no_skill=1.000 | ✓ no_skill=1.000 | 保持 |
| 盲区：移除mcp-builder | ✗ 技能创建 0.530 | ✓ no_skill=**0.950** | **翻转** |
| **总计** | **2/6** | **6/6** | **+4** |

核心改进：在 instructions 中明确**技能的本质是完成任务的指导说明/流程规范/最佳实践，不是语义相关的工具**。jev 看到"MCP"+"检查"关键词不应选"构建 MCP 服务"的技能——技能的文档化流程必须直接对应用户请求的具体内容。

**实验 3：state 含 catalog vs state 不含 catalog（5 场景对比）**

choice 的归一化分布天然要求 jev 同时看到所有选项。无论 description 放在 state 还是 criteria，jev 都能全局感知全部技能。两种方案表现相当，当前设计（description 在 criteria）token 成本更低。

---

## 1. 场景与问题

### 1.1 框架现状

harness 框架启动时扫描全部 skill，生成 catalog（name + description），将其**全量静态注入** system prompt，由主模型自行判断是否相关。

问题：
1. **token 开销随技能数线性增长**；
2. **相关性判断无校准信号**：主模型"看着办"，不可观测、不可调阈；
3. **误判代价不对称**：漏注入 → 能力缺失；全量注入 → 持续支付噪声 token。

### 1.2 工具定位

`skill_search` 是请求级决策点工具：**在 prompt 组装之前**，对"用户当前输入 × 候选技能"做相关性裁决，框架据此只注入/加载相关技能子集。

- **jev 只判断不生成**（原则 2）：技能扫描、触发词匹配等准备工作在框架侧完成。
- **只建议不执行**（原则 14）：工具返回概率与 verdict，是否注入由调用方决定。

---

## 2. 工具签名

### 2.1 输入

```json
{
  "query": "帮我做一份介绍公司 Q3 业绩的 PPT",
  "skills": [
    {"name": "ppt-architect", "description": "Create presentation slide decks..."},
    {"name": "html-ppt", "description": "Author professional HTML presentations..."},
    {"name": "pdf", "description": "Read, extract, merge and split PDF files."}
  ],
  "threshold": 0.3,
  "max_results": 5,
  "margin_threshold": 0.15,
  "exists_threshold": 0.3,
  "fits_threshold": 0.3,
  "switch_threshold": 50,
  "recall_top_k": 5,
  "description_max_chars": 500,
  "max_skills_per_batch": 100
}
```

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | string | 是 | — | 用户当前输入原文。工具不改写、不摘要。 |
| `skills` | array | 是 | — | 候选技能列表。每项 `{name, description}`。允许空数组（直接返回空结果）。 |
| `threshold` | float | 否 | `0.3` | 相关性概率阈值。技能场景取偏低值：漏选代价 > 多选代价。 |
| `max_results` | int | 否 | `5` | selected 返回条数上限。 |
| `margin_threshold` | float | 否 | `0.15` | 冠亚军概率差下限，不足则降级 review。 |
| `exists_threshold` | float | 否 | `0.3` | 两步模式 step1：max(noul 概率) 低于此值则 none_relevant，跳过 step2。 |
| `fits_threshold` | float | 否 | `0.3` | 两步模式 step2：max(fits) 低于此值则 none_fit。 |
| `switch_threshold` | int | 否 | `50` | skills ≤ 此值走单步，超过走两步。 |
| `recall_top_k` | int | 否 | `5` | 两步模式 step1 取 top-K 进入 step2。 |
| `description_max_chars` | int | 否 | `500` | description 防御性截断长度。 |
| `max_skills_per_batch` | int | 否 | `100` | 单批技能数硬上限。 |

所有阈值/限额均为可选参数带默认值，接入方需按自身数据调校（原则 11）。

### 2.2 输出

```json
{
  "mode": "auto",
  "steps": 1,
  "selected": [
    {"name": "ppt-architect", "probability": 0.91, "verdict": "select", "truncated": false}
  ],
  "reviewed": [],
  "skipped": [
    {"name": "pdf", "probability": 0.03, "verdict": "skip", "truncated": false}
  ],
  "invalid": [],
  "none_probability": 0.02,
  "thresholds": {"threshold": 0.3, ...},
  "batches": 1,
  "usage": {"input_tokens": 1850, "cost_usd": 0.000777, "credits_remaining_usd": 4.999223},
  "latency_ms": 412,
  "detail": {
    "pick_probabilities": {"s0": 0.91, "s1": 0.05, "s2": 0.02, "__no_skill_needed__": 0.02},
    "margin": 0.86,
    "overall_verdict": "auto_select"
  }
}
```

| 字段 | 说明 |
|---|---|
| `mode` | 固定 `"auto"`。 |
| `steps` | 1（单步）或 2（两步）。 |
| `selected` | verdict = `select` 的技能，按概率降序，≤ `max_results` 条。 |
| `reviewed` | verdict = `review`：截断、low_margin、below_threshold。 |
| `skipped` | verdict = `skip`：概率低于阈值或 none_relevant。 |
| `invalid` | verdict = `invalid_response`：jev 答案缺失/畸形。**绝不伪装成 skip 或 select。** |
| `none_probability` | choice 分布中 `__no_skill_needed__` 的概率。 |
| `thresholds` | 回显本次实际使用的全部阈值/限额。 |
| `batches` | /decide 调用批次数。 |
| `usage` | `input_tokens` / `cost_usd` / `credits_remaining_usd`，多批求和。 |
| `latency_ms` | 端到端耗时。 |
| `detail` | 诊断信息：`pick_probabilities`、`margin`、`overall_verdict`；两步模式额外含 `step1`（recall 概率、candidates）、`step2`（fits 值、margin）。 |

---

## 3. jev 归约

### 3.1 state 构造

state 只包含 `task`、`context`、`query`，**不含 catalog**——技能 name + description 内联到各自的 question 中（与 TypeSafe 设计一致）：

```json
{
  "task": "判断哪个候选技能最适合用户的查询，或者是否不需要任何技能。",
  "context": "用户发送了一条查询。助手有一份技能列表……技能是完成某类特定任务的指导说明、流程规范或最佳实践……",
  "query": "帮我做一份 PPT"
}
```

- `context` 定义技能的本质（指导说明/流程规范/最佳实践），引导 jev 做"流程对应"而非"关键词匹配"；
- 不携带 triggers、路径、版本等与判断无关的字段。

### 3.2 单步路径（skills ≤ switch_threshold）

一次 `/decide` 调用，仅含 1 个 choice 问题：

**`pick`（choice）**：criteria 包含全部技能 + `__no_skill_needed__` 选项：

```json
{
  "pick": {
    "type": "choice",
    "instructions": "state 包含一条用户查询……选出助手真正需要加载并遵循其指导来回答、解决或完成用户请求的那个选项……技能的文档化流程必须直接对应用户请求的具体内容……",
    "criteria": {
      "s0": "ppt-architect: Create presentation slide decks...",
      "s1": "html-ppt: Author professional HTML presentations...",
      "s2": "pdf: Read, extract, merge and split PDF files.",
      "__no_skill_needed__": "不需要候选技能中的任何一个。这意味着以下两种情况之一……"
    }
  }
}
```

**`__no_skill_needed__` 选项的作用**：同时看到 query + 全部技能描述，能判断两种"不需要技能"的情况：
- （a）查询可以纯文字回答（解释概念、给出建议）；
- （b）查询确实需要技能，但 候选技能集 中没有任何技能的指导覆盖该具体任务（如用户要求"检查 MCP 质量"但 候选技能集 只有"构建 MCP 服务"的技能）。

### 3.3 两步路径（skills > switch_threshold）

#### Step 1：noul 召回

每技能 1 个独立 noul，token 预算驱动分批并发。instructions 内联技能 name + description。

noul 概率降序取 top-K（`recall_top_k`）进入 step 2。

**存在性检查**：`max(noul 概率) < exists_threshold` → `none_relevant`，跳过 step 2。

#### Step 2：choice 精排 + fits noul

一次 `/decide` 调用：

- **`pick`（choice）**：criteria = top-K 技能 + `__no_skill_needed__` 选项；
- **`fits::{key}`（per-candidate noul）**：每个候选一个独立 noul，问"遵循此技能的具体指导是否直接对应用户请求的内容"。choice 决定 *which*，fits noul 决定 *whether*。

### 3.4 wire 标识：位置 key（原则 15）

- 进 jev 的所有标识（question 名、choice criteria key）一律用位置 key `s{i}`（i 为 skills 数组下标，跨批全局连续编号）；
- `__no_skill_needed__` 是固定的特殊 key，不与位置 key 冲突；
- 调用方传入的 `name` 原样透传进响应，不参与 wire key。

---

## 4. 判据与决策逻辑

### 4.1 阈值与默认值

| 参数 | 默认 | 语义 |
|---|---|---|
| `threshold` | 0.3 | 相关性通过线。 |
| `margin_threshold` | 0.15 | 冠亚军概率差下限。 |
| `exists_threshold` | 0.3 | 两步 step1：max(noul) 低于此则 none_relevant。 |
| `fits_threshold` | 0.3 | 两步 step2：max(fits) 低于此则 none_fit。 |
| `switch_threshold` | 50 | skills ≤ 此值单步，超过两步。 |
| `recall_top_k` | 5 | step1 取 top-K 进入 step2。 |
| `max_results` | 5 | selected 上限。 |

### 4.2 单步路径 verdict 计算

transport 层校验：pick 须 argmax、概率 ∈ [0,1]、probabilities keys 包含全部技能 key + `__no_skill_needed__`。任一校验失败 → 全部 invalid_response。

设冠军概率 `p1`、亚军概率 `p2`、`margin = p1 − p2`：

```
1. winner == __no_skill_needed__：
   → overall_verdict = "none_relevant"，全部技能 skip。
   → none_probability = p1。

2. p1 ≥ threshold 且 margin ≥ margin_threshold：
   → overall_verdict = "auto_select"。
   → 冠军 select（截断则 review）。
   → none_probability = probs[__no_skill_needed__]。

3. p1 ≥ threshold 但 margin < margin_threshold：
   → overall_verdict = "low_margin"。
   → 冠亚军均 review，reason = "low_margin"。

4. p1 < threshold：
   → overall_verdict = "below_threshold"。
   → 冠军 review，reason = "below_threshold"。
```

### 4.3 两步路径 verdict 计算

#### Step 1

- 每技能 noul 概率降序取 top-K；
- `max(noul 概率) < exists_threshold` → `none_relevant`，跳过 step 2；
- `none_probability = Π(1 − p_i)`（独立近似）。

#### Step 2

transport 层校验：pick 须 argmax；per-candidate fits noul 须合法。

设 `best_fits = max(fits)`、`winner_fits = fits[winner]`：

```
1. winner == __no_skill_needed__：
   → overall_verdict = "none_relevant"，全部 skip。

2. best_fits < fits_threshold：
   → overall_verdict = "none_fit"，全部 skip。
   （choice 选了某个技能，但 fits noul 显示没有候选真正 fit）

3. p1 ≥ threshold 且 margin ≥ margin_threshold 且 winner_fits ≥ fits_threshold：
   → overall_verdict = "auto_select"。
   → 冠军 select（截断则 review）。

4. p1 ≥ threshold 但 margin < margin_threshold：
   → overall_verdict = "low_margin"。冠亚军均 review。

5. p1 < threshold：
   → overall_verdict = "below_threshold"。冠军 review。

注：分支 3 要求 winner 自身的 fits ≥ fits_threshold；若 p1 与 margin 均达标但
fits[winner] < fits_threshold（即另一候选的 fits 更高），同样落入分支 5（below_threshold，
冠军 review），不会自动 select。
```

非 shortlist 的有效技能 → skip（reason = `not_in_shortlist`）。

`none_probability` = step 2 的 choice 分布中 `__no_skill_needed__` 的概率（如 winner 不是 no_skill）或 p1（如 winner 是 no_skill）。

### 4.4 fail-closed 行为汇总（原则 12）

**返回 `invalid_response`**：jev 答案缺失/畸形/概率越界/choice 非 argmax；批次请求失败且不可重试。

**绝不自动 select**：description 被截断的技能（即使概率达标，也只能 review）；概率未知或缺失。

---

## 5. 边界与错误处理

### 5.1 规模上限

| 边界 | 处理 |
|---|---|
| `skills` 为空 | 直接返回空结果 + `none_probability: null` + `usage` 全零，**不发请求**。 |
| `skills` 总数 | 无硬上限。单步受 choice criteria ≤ 255 约束（skills > switch_threshold 时自动走两步）。 |
| `query` 超长 | 不截断；计入 token 预算。极端情况返回明确错误。 |
| `description` 超长 | 头部截断至 `description_max_chars`，标记 `truncated: true`，永不自动 select。 |

### 5.2 token 预算驱动分批（原则 9）

jev 双预算：state + 全部 questions ≤ 64k tokens；state + 最长单 question ≤ 32k tokens。

保守估算（CJK × 1.0 + ASCII × 0.4，× 1.2 安全边际），贪心装填，多批并发，state 随每批重发，单批失败只影响该批。

### 5.3 错误处理（原则 10）

| 情况 | 处理 |
|---|---|
| 400 | 不重试。 |
| 401/402/403 | **绝不重试**。 |
| 408/429/5xx | 指数退避 + jitter 重试，最多 3 次。 |
| 歧义网络错误 | **绝不重发**（无幂等键，重发可能双倍计费）。 |
| 响应截断/JSON 畸形 | 批内全部 invalid_response。 |

---

## 6. 成本与延迟估算

**单步（skills ≤ 50）**：1 次 /decide（1 choice 含 `__no_skill_needed__`）。典型 15 技能 ≈ 1600 tokens ≈ $0.0007。

**两步（skills > 50）**：step1 = N 个 noul（可分批并发）；step2 = 1 次 /decide（1 choice + K 个 fits noul）。总成本 ≈ step1 + step2。

延迟：单步 ≈ 1 次往返（300–1000ms）；两步 ≈ 2 次往返串行（600–2000ms）。

---

## 7. 验收

### 7.1 真实 API 验收（6 场景，已通过）

使用当前测试环境的 15 个真实技能作为 候选技能集，4 个盲区场景移除应触发的技能：

| 场景 | query | 移除技能 | winner | no_skill p | 结果 |
|---|---|---|---|---|---|
| 明确命中 | 做PPT | 无 | html-pptx | 0.010 | ✓ |
| 盲区 | 创建新技能 | 技能创建 | no_skill_needed | **0.900** | ✓ |
| 盲区 | 检查MCP服务 | MCP检查 | no_skill_needed | **0.630** | ✓ |
| 盲区 | 审核设计文档 | 设计文档审核 | no_skill_needed | **0.730** | ✓ |
| 纯文字够用 | 解释Python装饰器 | 无 | no_skill_needed | **1.000** | ✓ |
| 盲区 | 构建MCP服务 | mcp-builder | no_skill_needed | **0.950** | ✓ |

6/6 准确率。详见 `docs/design/tools/no_skill_experiment.html`。

### 7.2 mock 测试要点（不烧 credits，原则 16）

- mock /decide 返回合法 answers，验证 choice 问题构造（criteria 含 `__no_skill_needed__`、position key）；
- 异常序列：502→200（重试成功）、连接重置（不重发）、401（零重试）；
- 预算分批：超预算自动分批、position key 全局连续、usage 求和；
- 畸形答案：缺失 key / 概率越界 / choice 非 argmax → invalid_response；
- 截断路径：description 截断 → 概率达标也仅 review。

---

## 8. 可观测性

### 8.1 stats

| 字段 | 说明 |
|---|---|
| `tool` | 固定 `"skill_search"` |
| `steps` | 1（单步）或 2（两步） |
| `skills_total` / `batches` | 候选数 / 批数 |
| `counts` | `{select, review, skip, invalid}` |
| `thresholds` | 本次实际使用的全部阈值 |
| `usage` | `input_tokens` / `cost_usd` / `credits_remaining_usd` |
| `latency_ms` | 端到端耗时 |

### 8.2 decisionLog

| 字段 | 说明 |
|---|---|
| `request_id` | 唯一 id |
| `skill_key` / `name` | 位置 key / 调用方 name |
| `probability` | choice 分布中该技能概率 |
| `none_probability` | `__no_skill_needed__` 的概率 |
| `margin` | 冠亚军概率差 |
| `verdict` | select / review / skip / invalid_response |
| `reason` | truncated / low_margin / below_threshold / none_relevant / none_fit / not_in_shortlist / network_ambiguous / invalid_envelope / … |
| `truncated` | description 是否被截断 |
