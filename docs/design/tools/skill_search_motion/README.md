# skill_search · 决策调用动画（GIF 剪辑版：组装 → 推理 → 输出）

`skill_search_flow.html` 的"动起来"版本：同一套视觉语言（纸色底、墨色文字、oklch 配色、衬线标题 + 等宽代码），按 baoyu-design animations-v3 引擎制作的 18 秒连续运动叙事。成品 GIF：`skill_search_motion.gif`（1280×720，13fps，234 帧，无限循环，2x 超采样录制保证文字锐度）。

## 预览

```bash
cd docs/design/tools
python3 -m http.server 8377
# 打开 http://localhost:8377/skill_search_motion/
```

必须走 HTTP（React + Babel 多文件结构），不要用 file:// 打开。播放位置持久化在 localStorage，刷新后从上次位置继续。

## 场景（总时长 18s；GIF 无限循环，结尾停留后直接回到组装，无淡场）

| 场景 | 时间 | 内容 |
|---|---|---|
| 1 · 组装 | 0–7s | query 卡与三张技能卡从左侧快速飞入 JSON 载荷（state.query 行高亮、criteria s0/s1/s2 逐行点亮、琥珀色 `__no_skill_needed__` 逃生舱弹出） |
| 2 · 推理 | 7–11s | 载荷收缩进深色「决策模型」节点，脉冲后吐出应答芯片 |
| 3 · 输出 | 11–18s | 概率条逐条生长 + 数字滚动（0.91 select / 0.05 / 0.02 skip / 0.02 逃生舱），标注 none_probability 与 margin 0.86，停留至结尾 |

## GIF 生成

逐帧录制（CDP sync seek，2x 超采样：窗口 2560×1528 使舞台缩放恰好为 2，PNG 帧为 2560×1440，裁剪到 svg 舞台）：

```bash
node record.mjs   # 见下文说明，输出 /tmp/ssm_frames2/frame_%04d.png（234 帧 @13fps，2560×1440）
```

两-pass 调色板合成（超采样缩小 + 文字友好抖动，保证清晰度）：

```bash
ffmpeg -y -framerate 13 -i /tmp/ssm_frames2/frame_%04d.png \
  -vf "fps=13,scale=1280:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=256:stats_mode=full[p];[s1][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle" \
  -loop 0 skill_search_motion.gif
```

- 清晰度关键：PNG 帧 2560×1440 → lanczos 缩到 1280（2x 超采样）；`dither=sierra2_4a` 比 bayer 更适合文字边缘
- `-loop 0`：无限循环；`diff_mode=rectangle` 只存帧间差分区域（成品约 2.0MB）
- 如需更小：降 `fps` 到 12、`scale` 到 1152，或退回 `dither=bayer:bayer_scale=3`

`record.mjs` 是一个 CDP 脚本（Node ≥ 22，内置 WebSocket）：启动 chrome-headless-shell，对 `data-om-exportable-video-with-duration-secs` 根元素逐帧派发 `data-om-seek-to-time-frame`（`sync: true`）并裁剪截图。页面本身满足 animations-v3 导出协议，在 baoyu 宿主环境也可 Share → Export → Video 直接导出 MP4。

## 事实来源

全部文案与数据结构来自 `src/jev_harness_tool/tools/skill_search.py` 与 `docs/design/tools/skill_search_flow.html`，未编造。

## 文件

- `index.html` — 入口；`window.OM_SCENES` / `window.OM_PLAYBACK` 场景与播放字面量（宿主时间轴编辑器的写回锚点）
- `piece.jsx` — 全部编排（单元素树，一切 keyed 到 T/CUES）
- `animations-v3.jsx` / `tweaks-panel.jsx` — baoyu-design 引擎脚手架（勿改）
- `vendor/` — react / react-dom / babel-standalone 本地化，离线可预览
- `screenshots/` — 各场景边界 ±0.15s 的自查截图
- `skill_search_motion.gif` — 成品（1280×720 · 13fps · 234 帧 · 无限循环 · 2x 超采样录制）
