# reference_verify · 内联引用验证动画（深色「证据审查室」）

RAG/Agent 答案中的内联引用 [标题](URL) 可能不成立——文章不支持、只沾边、甚至矛盾。`reference_verify` 在答案发出前逐对验证 claim ↔ passage。本目录是它的视频级连续运动叙事（animations-v3 引擎），深色审查室风格：完整 state 面板居中呈证、文档字段卡逐一归位、扫描光束检验后 verdict 以印章形式落下（allow 绿 / review 琥珀 / block 红）。成品 GIF：`reference_verify_motion.gif`（1280×720，13fps，312 帧，无限循环，2x 超采样录制）。

## 预览

```bash
cd docs/design/tools
python3 -m http.server 8377
# 打开 http://localhost:8377/reference_verify_motion/
```

必须走 HTTP（React + Babel 多文件结构），不要用 file:// 打开。播放位置持久化在 localStorage。

## 场景（总时长 24s；GIF 无限循环，结尾停留在双 verdict 画面后直接循环）

| 场景 | 时间 | 内容 |
|---|---|---|
| 1 · 疑点 | 0–2.5s | 带内联引用的模型答案卡出现，引用 chip 旁悬置问号 |
| 2 · 组装 | 2.5–12.5s | **核心画面**：完整 state 面板（task / pairs / p0{claim, passage, context} / p1{claim, passage}，17 行全部可读）。claim 卡（英雄元素）飞入 `p0.claim` 行点亮高亮；引用 chip morph 进 `p0.passage` 行；context 卡（琥珀边框，标注可选）飞入 `p0.context` 行；p1 无 context 保持两键（行内注释点出）；右侧 questions 列生成每对两题；wire key p0/p1 描边强调；p1 的 23% 琥珀脉冲埋伏笔 |
| 3 · 推理 | 12.5–16s | state 面板整体收进「决策模型」节点，一次 /decide 调用返回概率 |
| 4 · 裁决 | 16–24s | 双面板扫描光束后落戳：p0 support 0.93 + direct_support 0.88 → 绿戳 supported · allow；p1 contradicts 0.81、margin 0.72 达标 → 红戳 contradicted · block（矛盾优先于一切支持度信号）；停留供循环 |

演示数据：p0 claim「2024 年全球可再生能源发电量首次超过煤电，占比达 32%。」 vs passage（数字一致）；p1 claim「……占比达 32%」 vs passage「……占比为 23%……」。state 结构与字段（含 p0 带 context、p1 不带）与 `reference_verify.py` 的 `_decide_batch` 一致；阈值 support 0.7 / margin 0.15 与短路判定顺序同源码。

## GIF 生成

逐帧录制（CDP sync seek，2x 超采样：窗口 2560×1528，PNG 帧 2560×1440，裁剪到 svg 舞台）：

```bash
node record.mjs   # 输出 /tmp/rvm_frames/frame_%04d.png（312 帧 @13fps，2560×1440）
```

两-pass 调色板合成（超采样缩小 + 文字友好抖动）：

```bash
ffmpeg -y -framerate 13 -i /tmp/rvm_frames/frame_%04d.png \
  -vf "fps=13,scale=1280:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=256:stats_mode=full[p];[s1][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle" \
  -loop 0 reference_verify_motion.gif
```

- `-loop 0`：无限循环；`diff_mode=rectangle` 只存帧间差分区域（成品约 3.4MB）
- 如需更小：降 `fps` 到 12、`scale` 到 1152，或退回 `dither=bayer:bayer_scale=3`

`record.mjs` 是 CDP 脚本（Node ≥ 22，内置 WebSocket）：启动 chrome-headless-shell，对 `data-om-exportable-video-with-duration-secs` 根元素逐帧派发 `data-om-seek-to-time-frame`（`sync: true`）并裁剪截图。页面满足 animations-v3 导出协议，在 baoyu 宿主环境也可 Share → Export → Video 导出 MP4。

## 事实来源

全部文案与数据结构来自 `src/jev_harness_tool/tools/reference_verify.py` 与 `docs/design/tools/reference_verify.md`；passage/context 示例文本是为演示编写的合理短文。

## 文件

- `index.html` — 入口；`window.OM_SCENES` / `window.OM_PLAYBACK` 字面量（宿主时间轴编辑器写回锚点）
- `piece.jsx` — 全部编排（单元素树，一切 keyed 到 T/CUES）
- `animations-v3.jsx` / `tweaks-panel.jsx` — baoyu-design 引擎脚手架（勿改）
- `vendor/` — react / react-dom / babel-standalone 本地化
- `record.mjs` — 逐帧录制脚本
- `screenshots/` — 各场景边界 ±0.15s 的自查截图
- `reference_verify_motion.gif` — 成品（1280×720 · 13fps · 312 帧 · 无限循环）
