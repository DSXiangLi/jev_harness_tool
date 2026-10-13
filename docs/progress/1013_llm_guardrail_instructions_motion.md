# input_llm_guardrail v3 措辞精简 + 工具动画 GIF

> 日期：2026-10-13

## 变更

1. **input_llm_guardrail instructions/criteria 去重**（与 skill_search v2.1 同一原则）：5 个 hazard（pii_exposure / credential_leak / data_exfiltration / indirect_injection / regulated_advice）的 instructions 收敛为「防注入声明 + 一句简洁判断问题」，详细定义收进 criteria true/false，不再在 instructions 中重复。设计文档 §3.1 表格同步为逐字措辞。
2. **llm_guardrail 视频级动画**：`docs/design/tools/llm_guardrail_motion/`（animations-v3 引擎，工业安全标识风），22s 叙事：消息抵达 → 组装（完整 state + 9 noul 探测器阵列）→ 推理 → 裁决（jailbreak 0.98 冲阈值线、优先级 support>block>review>pass、信号灯塔落 block）。录制 1280×720 GIF（13fps、286 帧、2.6MB、无限循环）。
3. **README 嵌入动画 GIF**：三个已验证/有动画的工具各嵌一张——skill_search（浅色纸面风）、reference_verify（深色审查风）、input_llm_guardrail（工业安全风）。

## 验证

- `pytest tests/test_input_llm_guardrail.py -m "not real"`：39 项 mock 全绿（2 项 real 需 credits，未跑）
- 动画自查：几何断言 20 个时间点 + GIF 帧计数/循环校验通过
