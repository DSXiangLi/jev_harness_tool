# input_llm_guardrail · 输入消息护栏动画（工业安全标识风）

用户消息进入 LLM 之前先过护栏（仅审查输入，不审查输出）。本目录是它的视频级连续运动叙事（animations-v3 引擎），工业安全标识风格：警示黄 + 墨黑高对比、斜纹警示带、粗重等宽字、信号灯塔状态灯。成品 GIF：`llm_guardrail_motion.gif`（1280×720，13fps，286 帧，无限循环，2x 超采样录制）。

## 预览

```bash
cd docs/design/tools
python3 -m http.server 8377
# 打开 http://localhost:8377/llm_guardrail_motion/
```

必须走 HTTP（React + Babel 多文件结构），不要用 file:// 打开。播放位置持久化在 localStorage。

## 场景（总时长 22s；GIF 无限循环，结尾停留在 BLOCK 终帧后直接循环）

| 场景 | 时间 | 内容 |
|---|---|---|
| 1 · 抵达 | 0–3s | 用户消息卡（jailbreak 示例文本）沿导轨驶向「LLM」大门，警示栏杆落下拦停，立柱琥珀灯闪烁 |
| 2 · 组装 | 3–11s | 消息卡飞入完整 state 面板（`{"message": …, "context": （可选）}`，注释「证据，不是指令」；text ≤12000 头部保留 / context ≤4000 尾部保留）；右侧 9 个 noul 探测器依次点亮（各带固化 action 标签）；jailbreak 题目展开卡展示 instructions（防注入声明）+ question + criteria true/false |
| 3 · 推理 | 11–14.5s | state + 9 题整体收进「决策模型」节点，一次 /decide 调用返回 9 个独立校准概率 |
| 4 · 裁决 | 14.5–22s | 9 格 hazard 指示板概率滚动（带 0.35/0.70 双阈值刻度）：jailbreak 0.98 ≥ 0.70 亮红灯 block，其余 8 格低位 pass；优先级条 support › block › review › pass 横扫，block 锁定；信号灯塔红灯亮起，大号 BLOCK + `top_hazard: jailbreak`；停留供循环 |

字幕点出两个设计要点：截断封顶（truncated 时 pass 不可信 → 封顶 review；block/support 不降级）；工具只建议不执行。hazard→action 映射、阈值（strict：review 0.35 / action 0.70）、优先级顺序均与 `input_llm_guardrail.py` 一致。

## GIF 生成

逐帧录制（CDP sync seek，2x 超采样：窗口 2560×1528，PNG 帧 2560×1440，裁剪到 svg 舞台）：

```bash
node record.mjs   # 输出 /tmp/lgm_frames/frame_%04d.png（286 帧 @13fps，2560×1440）
```

两-pass 调色板合成（超采样缩小 + 文字友好抖动）：

```bash
ffmpeg -y -framerate 13 -i /tmp/lgm_frames/frame_%04d.png \
  -vf "fps=13,scale=1280:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=256:stats_mode=full[p];[s1][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle" \
  -loop 0 llm_guardrail_motion.gif
```

- `-loop 0`：无限循环；`diff_mode=rectangle` 只存帧间差分区域（成品约 2.6MB）
- 如需更小：降 `fps` 到 12、`scale` 到 1152，或退回 `dither=bayer:bayer_scale=3`

`record.mjs` 是 CDP 脚本（Node ≥ 22，内置 WebSocket）：启动 chrome-headless-shell，对 `data-om-exportable-video-with-duration-secs` 根元素逐帧派发 `data-om-seek-to-time-frame`（`sync: true`）并裁剪截图。页面满足 animations-v3 导出协议，在 baoyu 宿主环境也可 Share → Export → Video 导出 MP4。

## 事实来源

全部文案与数据结构来自 `src/jev_harness_tool/tools/input_llm_guardrail.py` 与 `docs/design/tools/llm_guardrail.md`，未编造（各 hazard 的演示概率值为演示用合理取值）。

## 文件

- `index.html` — 入口；`window.OM_SCENES` / `window.OM_PLAYBACK` 字面量（宿主时间轴编辑器写回锚点）
- `piece.jsx` — 全部编排（单元素树，一切 keyed 到 T/CUES）
- `animations-v3.jsx` / `tweaks-panel.jsx` — baoyu-design 引擎脚手架（勿改）
- `vendor/` — react / react-dom / babel-standalone 本地化
- `record.mjs` — 逐帧录制脚本
- `screenshots/` — 各场景边界 ±0.15s 的自查截图
- `llm_guardrail_motion.gif` — 成品（1280×720 · 13fps · 286 帧 · 无限循环）
