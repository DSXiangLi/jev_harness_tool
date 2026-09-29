# Jev 模型 API 文档

> 来源：https://jevtypesafeai.com/docs
> 整理日期：2026-09-28
> 说明：Jev 是一个通用**决策 API**。发送一个 `state` 和一组带类型的 `questions` 到 `/v1/decide`，返回校准过的 `answers`。下方各 ready-made 端点只是常见任务的快捷方式，本质都是固定问题集的 `/decide` 调用。

## 认证

所有端点使用同一个 Bearer key（形如 `jv_live_…`）。key 必须保存在服务端环境变量中，禁止提交到代码库或暴露到客户端。

```
Authorization: Bearer jv_live_your_key_here
Content-Type: application/json
```

## 核心 API — POST /v1/decide

端点：`POST https://jevtypesafeai.com/api/v1/decide`

一次调用：一个 state + 一组带类型的问题，并行评估，一个往返返回校准答案。

### 请求体

| 字段 | 说明 | 必填 |
|---|---|---|
| `model` | `jev-latest`，或固定版本如 `jev-1.13.0` | 可选 |
| `state` | 待评估的输入：字符串、对象或数组 | 必填 |
| `questions` | 名字 → 带类型问题（choice / score / noul）的 map，一次调用可混合任意多个 | 必填 |

### 三种问题类型

每个问题恰好是以下三种之一，可在一次调用中混合。

#### 1. choice — 从选项中选一个

`criteria` 为最多 255 个带标签选项的 map。返回获胜选项、每个选项的概率、置信度。

```json
"route": {
  "type": "choice",
  "instructions": "Where should this ticket go?",
  "criteria": {
    "billing": "payments, refunds, invoices",
    "bug": "the product is broken",
    "account": "login or access"
  }
}
```

返回：

```json
{ "type": "choice", "choice": "billing", "confidence": 0.99,
  "probabilities": { "billing": 0.99, "bug": 0.0, "account": 0.01 } }
```

#### 2. score — 在有序刻度上评分

`criteria` 为 2–10 个具体等级描述的有序数组（从低到高）。返回（可能为小数的）分数、各等级概率、图例、置信度。

```json
"urgency": {
  "type": "score",
  "instructions": "How urgent is this message?",
  "criteria": ["routine, no rush", "today", "urgent", "critical, about to churn"]
}
```

返回：

```json
{ "type": "score", "score": 2.97, "confidence": 1.0,
  "probabilities": { "0": 0.0, "3": 1.0 } }
```

#### 3. noul — 校准的是/否

只需 instructions。返回 `noul`：答案为"是"的校准 0–1 概率。适合用作门控（gate）和护栏（guardrail）。

```json
"escalate": {
  "type": "noul",
  "instructions": "Escalate to a human immediately?"
}
```

返回：`{ "type": "noul", "noul": 0.94 }`

### 输入预算

- `state` 可以是字符串、对象或数组——只发送决策所需的上下文。
- 两条输入预算限制：
  - state + 全部 questions 合计 ≤ 64k tokens
  - state + 最长单个 question ≤ 32k tokens
- 超限会上游返回 `max_tokens_exceeded` —— 需要裁剪 state 或拆分任务，服务端不会帮你截断。

### 完整请求示例

```bash
curl https://jevtypesafeai.com/api/v1/decide \
  -H "Authorization: Bearer $JEV_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "state": "Customer: I was charged twice and nobody has replied for 3 days.",
    "questions": {
      "route":    { "type": "choice", "instructions": "Where should this go?",
                    "criteria": { "billing": "money", "bug": "broken", "account": "login" } },
      "urgency":  { "type": "score",  "instructions": "How urgent is this?",
                    "criteria": ["routine", "today", "urgent", "critical"] },
      "escalate": { "type": "noul",   "instructions": "Escalate to a human now?" }
    }
  }'
```

### 响应

返回解析后的模型版本、与问题一一对应的 `answers` map、以及 `usage` 块。每个 answer 的类型由请求固定，可直接在代码中分支——无需解析、无需正则。

```json
{
  "model": "jev-1.13.0",
  "answers": {
    "route":    { "type": "choice", "choice": "billing", "confidence": 0.99,
                  "probabilities": { "billing": 0.99, "bug": 0.0, "account": 0.01 } },
    "urgency":  { "type": "score", "score": 3.0, "probabilities": { "0": 0.0, "3": 1.0 } },
    "escalate": { "type": "noul", "noul": 0.94 }
  },
  "usage": { "input_tokens": 62, "cost_usd": 0.000026, "credits_remaining_usd": 4.999974 }
}
```

### 错误码

| 状态码 | 含义 |
|---|---|
| 400 | 请求未通过校验，`error` 字段说明原因 |
| 401 | API key 缺失、无效或已吊销 |
| 402 | 余额不足（code: `insufficient_credits`） |
| 403 | 账户未激活 |
| 404 | 未知的应用端点 |
| 502 | 上游 Jev 错误；需退避重试 |

### 最佳实践

- 有合适的 ready-made 端点时优先使用；自定义决策再用 `/decide`。
- 裁剪 state 只保留决策所需内容——按输入 token 计费。
- 生产环境固定模型版本（如 `jev-1.13.0`），避免阈值漂移。
- `jv_live_` key 只放服务端。

## Ready-made APIs（快捷端点）

以下端点都是可选的快捷方式：字段不合规返回 400 并附 `expects` 示例。没有合适的就用 `/v1/decide`。

### Email — POST /api/v1/email/triage

邮件分拣：类别、优先级、是否垃圾邮件、是否需要回复、路由。

```json
// request
{"subject": "Charged twice", "body": "I was charged twice and need this fixed today."}
// response
{"category": "billing", "priority": "urgent", "spam": false, "needs_reply": true,
 "route_to": "finance", "confidence": 0.96}
```

### Support — POST /api/v1/support/triage

工单路由：团队、问题类型、严重度、紧急度、是否升级。

```json
// request
{"subject": "App is down", "body": "Production dashboard returns 500 for all users since 10am."}
// response
{"team": "technical", "issue_type": "outage", "severity": "critical",
 "urgency": "now", "escalate": true, "confidence": 0.99}
```

### Agents — POST /api/v1/agent/risk

工具调用风险门控：allow / confirm / block + 0–1 风险分 + 风险类别。

```json
// request
{"goal": "Clean up the build directory", "tool": "bash",
 "arguments": "rm -rf ./dist && aws s3 sync ./build s3://prod --delete", "context": "CI deploy step"}
// response
{"action": "block", "risk": 0.84,
 "categories": ["destructive", "irreversible", "external_side_effect", "data_exposure"],
 "confidence": 0.6}
```

### Coding agents — POST /api/v1/context/filter

上下文过滤：对旧的上下文条目做 keep / truncate / drop，对抗 agent 上下文膨胀。

```json
// request
{"task": "Fix the failing payment webhook test",
 "item": "Tool call: read_file('README.md') -> 4000 tokens of project overview from 30 steps ago"}
// response
{"action": "drop", "relevance": "minor", "redundant": true, "confidence": 0.53}
```

### LLM — POST /api/v1/model/route

模型路由：按 prompt 复杂度选择模型档位。传 `models[]` 则返回具体模型名。

```json
// request
{"prompt": "Summarize this 2-sentence email in one line.",
 "models": ["fast-mini", "balanced", "frontier"]}
// response
{"recommended_tier": "fast", "complexity": "simple",
 "recommended_model": "fast-mini", "confidence": 0.78}
```

### RAG — POST /api/v1/rag/relevance

RAG 相关性：判断检索到的段落是否真正回答了查询，用于重排与过滤。

```json
// request
{"query": "How do I rotate my API key?",
 "passage": "To rotate a key, open Settings > API keys, click Revoke on the old key, then Create new key. Update your environment variable."}
// response
{"relevant": true, "relevance": "direct answer", "supports_claim": true, "confidence": 1.0}
```

### Sales — POST /api/v1/leads/qualify

线索资格判定：是否合格、ICP 匹配度、细分市场、是否正在购买、路由。

```json
// request
{"lead": "Jane Doe, VP Eng at Acme (500 employees). 'We're evaluating decision APIs...'"}
// response
{"qualified": true, "icp_match": "ideal", "segment": "mid_market",
 "buying_now": true, "route": "sales", "confidence": 0.83}
```

### Moderation — POST /api/v1/content/moderate

内容审核：allow / review / block + 分类标记 + 违规类型。

```json
// request
{"text": "You're an idiot and I'll find where you live."}
// response
{"action": "block",
 "flags": {"toxicity": true, "harassment": true, "violence": true, "sexual": false,
           "self_harm": false, "spam": false, "fraud": false, "pii": false},
 "violation_types": ["toxicity", "harassment", "violence"], "confidence": 0.69}
```

### Content — POST /api/v1/content/classify

内容打标：主题、格式、hook 风格、语气、互动潜力。

```json
// request
{"text": "I quit my $200k job to sell candles..."}
// response
{"topic": "business", "format": "listicle", "hook_style": "bold_claim",
 "tone": "inspirational", "engagement_potential": "very high", "confidence": 0.82}
```

### Ads — POST /api/v1/ads/analyze

广告分析：hook 类型、认知阶段、是否有明确 offer、CTA、摩擦度。

```json
// request
{"headline": "Stop losing leads to slow follow-up", "primary_text": "...", "cta": "Start free trial"}
// response
{"hook_type": "problem", "awareness_stage": "problem_aware", "has_clear_offer": true,
 "has_cta": true, "friction": "low", "confidence": 0.72}
```

### SEO — POST /api/v1/seo/page-relevance

SEO 页面相关性：判断一个页面是否应内链到另一个页面，以及二者关系。

```json
// request
{"source": "Blog: 'How calibrated confidence scores work in decision models'",
 "target": "Docs: 'noul — a calibrated yes/no question type'"}
// response
{"should_link": true, "relevance": "related", "relationship": "narrower", "confidence": 0.62}
```

## 计费

- 预付费 credits，核心 Decision API 与所有 ready-made 端点共用一个余额。
- $0.42 / 1M 输入 tokens（标准 credits；大额套餐单价更低）。**输出 tokens 免费**。
- 每个响应都带 `cost_usd` 和 `credits_remaining_usd`。
- Credits 永不过期。

## 附注

- 一个 API key + 一个计费余额覆盖所有工作流。
- 支持批量工作流，官方提供 curl / Python / JS 示例。
- 独立服务：JevTypeSafeAI.com 与 TypeSafe AI 无隶属或背书关系。
