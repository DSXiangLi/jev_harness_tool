/* piece.jsx — skill_search 决策调用 · 连续运动叙事（GIF 剪辑版：组装 → 推理 → 输出）
   事实来源：src/jev_harness_tool/tools/skill_search.py
   英雄元素：用户 query 卡片（开场从左侧飞入载荷的 state.query 行）
   全部编排 keyed 到 useComposition() 的 T 与 CUES。 */

const C = {
  paper: 'oklch(98.5% 0.004 90)',
  ink: 'oklch(24% 0.02 260)',
  ink2: 'oklch(46% 0.02 260)',
  line: 'oklch(88% 0.01 260)',
  select: 'oklch(55% 0.13 155)',
  review: 'oklch(64% 0.14 75)',
  skip: 'oklch(62% 0.01 260)',
  accent: 'oklch(45% 0.09 250)',
  white: 'oklch(100% 0 0)',
  codeBg: 'oklch(20% 0.02 260)',
  codeText: 'oklch(86% 0.02 260)',
  codeKey: 'oklch(80% 0.09 230)',
  codeStr: 'oklch(83% 0.09 145)',
  codeCmt: 'oklch(58% 0.02 260)',
  codeEsc: 'oklch(76% 0.13 75)',
};
const SERIF = '"Songti SC","Noto Serif SC","Source Han Serif SC",Georgia,serif';
const MONO = 'ui-monospace,"SF Mono","Cascadia Code",Menlo,Consolas,monospace';
const SANS = '-apple-system,"PingFang SC","Noto Sans SC","Microsoft YaHei",sans-serif';

// 三个运动原语，全片只用这三条曲线
const MOTION = { enter: Easing.easeOutCubic, draw: Easing.easeInOutCubic, pop: Easing.easeOutBack };
const seg = (T, a, b, ease) => animate({ from: 0, to: 1, start: a, end: b, ease: ease || MOTION.draw })(T);
const mix = (a, b, k) => a + (b - a) * k;

// ── 事实数据 ────────────────────────────────────────────────────────────────
const QUERY = '帮我做一份介绍公司 Q3 业绩的 PPT';
const SKILLS = [
  { key: 's0', name: 'ppt-architect', desc: 'Create presentation slide decks and PPT files.' },
  { key: 's1', name: 'html-ppt', desc: 'Author professional HTML presentations.' },
  { key: 's2', name: 'pdf', desc: 'Read, extract, merge and split PDF files.' },
];
const RESULTS = [
  { name: 'ppt-architect', p: 0.91, verdict: 'select', color: C.select },
  { name: 'html-ppt', p: 0.05, verdict: 'skip', color: C.skip },
  { name: 'pdf', p: 0.02, verdict: 'skip', color: C.skip },
  { name: '__no_skill__', p: 0.02, verdict: '逃生舱', color: C.review },
];

// ── 布局常量 ────────────────────────────────────────────────────────────────
const Q_CARD = { w: 470, h: 118 };                            // query 卡片自然尺寸
const SKILL_CARD = { w: 460, h: 104 };                        // 技能卡自然尺寸
const PANEL = { x: 150, y: 52, w: 980, h: 616, padX: 30, padY: 24, lh: 27 }; // 载荷
const lineY = (i) => PANEL.y + PANEL.padY + i * PANEL.lh;
const Q_LINE = { x: 289, y: lineY(6) - 4, w: 320, h: 32 };    // state.query 行高亮区
const CRIT_LINE = (j) => ({ x: 300, y: lineY(12 + j) - 4, w: 580, h: 32 }); // criteria s_j 行
const NODE = { x: 450, y: 255, w: 380, h: 150 };              // 决策模型节点
const BARS = { x: 170, y: 88, w: 940, h: 544 };               // 输出卡片
const ROW_Y = (i) => 196 + i * 62;
const TRACK = { x: 420, w: 560, h: 16 };

// ── 载荷文本（逐行，含语法着色分段）─────────────────────────────────────────
const K = (t) => ({ t, c: C.codeKey });
const S = (t) => ({ t, c: C.codeStr });
const P = (t) => ({ t, c: C.codeText });
const CM = (t) => ({ t, c: C.codeCmt, it: true });
const ESC = (t) => ({ t, c: C.codeEsc, b: true });
const PAYLOAD_LINES = [
  [P('{')],
  [P('  '), K('"state"'), P(': {'), CM('      // 共享上下文：只描述任务，不含候选技能集')],
  [P('    '), K('"task"'), P(': '), S('"判断哪个候选技能最适合用户的查询，或者是否不需要任何技能。"'), P(',')],
  [P('    '), K('"context"'), P(': '), S('"用户发送了一条查询。助手有一份候选技能列表，每个技能有名称和描述。"')],
  [P('      '), S('"技能是完成某类特定任务的指导说明、流程规范或最佳实践——"')],
  [P('      '), S('"不是仅仅与查询共享关键词的工具或 API。加载技能意味着遵循其文档化的流程来处理请求。"'), P(',')],
  [P('    '), K('"query"'), P(': '), S('"' + QUERY + '"')],
  [P('  },')],
  [P('  '), K('"questions"'), P(': {'), CM('  // 问题集：本例 1 道单选题')],
  [P('    '), K('"pick"'), P(': {')],
  [P('      '), K('"type"'), P(': '), S('"choice"'), P(',')],
  [P('      '), K('"criteria"'), P(': {'), CM('  // 候选技能集内联为选项')],
  [P('        '), K('"s0"'), P(': '), S('"ppt-architect: Create presentation slide decks…"'), P(',')],
  [P('        '), K('"s1"'), P(': '), S('"html-ppt: Author professional HTML presentations…"'), P(',')],
  [P('        '), K('"s2"'), P(': '), S('"pdf: Read, extract, merge and split PDF files."'), P(',')],
  [P('        '), ESC('"__no_skill_needed__"'), P(': '), ESC('"不需要任何候选技能：纯文字可答，或候选集中没有覆盖该任务的技能"')],
  [P('      }')],
  [P('    }')],
  [P('  }')],
  [P('}')],
];
// 技能卡 j 落线时刻 / 逃生舱行弹出时刻
const LAND = (j) => 2.4 + j * 0.3;
const ESC_POP = 3.3;

function lineOpacity(T, i) {
  if (i <= 5) return seg(T, 0.35 + i * 0.1, 0.65 + i * 0.1, MOTION.enter);
  if (i === 6) return 0.22 + 0.78 * seg(T, 1.35, 1.65, MOTION.enter); // query 行：先淡，英雄落定后点亮
  if (i <= 11) return seg(T, 1.2 + (i - 7) * 0.09, 1.5 + (i - 7) * 0.09, MOTION.enter);
  if (i <= 14) { const j = i - 12; return 0.16 + 0.84 * seg(T, LAND(j), LAND(j) + 0.22, MOTION.enter); }
  if (i === 15) return seg(T, ESC_POP, ESC_POP + 0.3, MOTION.pop);
  return seg(T, 3.5 + (i - 16) * 0.09, 3.75 + (i - 16) * 0.09, MOTION.enter);
}

// ── 通用小件 ────────────────────────────────────────────────────────────────
function Tag({ text, color, style }) {
  return (
    <span style={Object.assign({
      display: 'inline-block', fontFamily: MONO, fontSize: 10, fontWeight: 700,
      letterSpacing: '0.06em', color, border: '1px solid ' + color,
      borderRadius: 4, padding: '0 5px', lineHeight: '16px', verticalAlign: '2px',
    }, style)}>{text}</span>
  );
}

// ── 场景标签（右上，随场景切换）─────────────────────────────────────────────
const STAGE_LABELS = ['1 · 组装', '2 · 推理', '3 · 输出'];
function StageLabels({ T, CUES }) {
  const cues = [CUES.Assemble, CUES.Infer, CUES.Output, 18];
  return (
    <div style={{ position: 'absolute', right: 80, top: 72, textAlign: 'right' }}>
      {STAGE_LABELS.map((lb, i) => {
        const o = seg(T, cues[i] + 0.05, cues[i] + 0.45, MOTION.enter) * (1 - seg(T, cues[i + 1] - 0.55, cues[i + 1] - 0.15));
        return (
          <div key={lb} style={{
            position: 'absolute', right: 0, top: 0, opacity: o, whiteSpace: 'nowrap',
            fontFamily: SERIF, fontSize: 15, fontWeight: 700, letterSpacing: '0.18em', color: C.ink2,
          }}>{lb}</div>
        );
      })}
    </div>
  );
}

// ── 英雄：query 卡片（开场从左侧飞入载荷的 query 行）────────────────────────
function HeroQueryCard({ T }) {
  const fly = seg(T, 0.25, 1.25, MOTION.draw);
  const gone = seg(T, 1.15, 1.45, MOTION.draw);
  const x = mix(-520, Q_LINE.x, fly);
  const y = mix(Q_LINE.y - 40, Q_LINE.y, fly);
  const s = mix(0.55, Q_LINE.h / Q_CARD.h, fly);
  const opacity = seg(T, 0.25, 0.5, MOTION.enter) * (1 - gone);
  return (
    <div data-role="hero" style={{
      position: 'absolute', left: 0, top: 0, width: Q_CARD.w, height: Q_CARD.h,
      transform: `translate(${x}px, ${y}px) scale(${s})`, transformOrigin: 'left top',
      opacity,
    }}>
      <div style={{
        position: 'absolute', inset: 0, background: C.white, border: '1px solid ' + C.line,
        borderRadius: 10, boxShadow: '0 6px 22px oklch(24% 0.02 260 / 0.12)',
        padding: '16px 20px', boxSizing: 'border-box',
      }}>
        <div style={{ fontFamily: MONO, fontSize: 12 }}>
          <span style={{ fontWeight: 700, color: C.accent }}>query</span>
          <span style={{ color: C.ink2 }}> · string</span>
          <Tag text="必填" color={C.accent} style={{ marginLeft: 8 }} />
        </div>
        <div style={{ fontFamily: SANS, fontSize: 20, color: C.ink, marginTop: 12, lineHeight: 1.5 }}>
          {QUERY}
        </div>
      </div>
    </div>
  );
}

// ── 技能卡（从左侧依次飞入 criteria 行）────────────────────────────────────
function SkillCards({ T }) {
  return SKILLS.map((sk, j) => {
    const a = 1.55 + j * 0.3;                      // 起飞
    const fly = seg(T, a, a + 0.85, MOTION.draw);
    const gone = seg(T, LAND(j) - 0.12, LAND(j) + 0.12, MOTION.draw);
    const target = CRIT_LINE(j);
    const x = mix(-520, target.x, fly);
    const y = mix(target.y - 30, target.y, fly);
    const s = mix(0.5, target.h / SKILL_CARD.h, fly);
    const opacity = seg(T, a, a + 0.25, MOTION.enter) * (1 - gone);
    return (
      <div key={sk.key} data-role={'skill-' + j} style={{
        position: 'absolute', left: 0, top: 0, width: SKILL_CARD.w, height: SKILL_CARD.h,
        transform: `translate(${x}px, ${y}px) scale(${s})`, transformOrigin: 'left top',
        opacity,
      }}>
        <div style={{
          position: 'absolute', inset: 0, background: C.white, border: '1px solid ' + C.line,
          borderRadius: 10, padding: '15px 18px', boxSizing: 'border-box',
          boxShadow: '0 4px 16px oklch(24% 0.02 260 / 0.1)',
        }}>
          <div style={{ fontFamily: MONO, fontSize: 15, fontWeight: 700, color: C.accent }}>
            {sk.name}
            <span style={{
              marginLeft: 10, fontSize: 10.5, fontWeight: 600, color: C.ink2,
              border: '1px solid ' + C.line, borderRadius: 4, padding: '1px 6px',
            }}>{sk.key}</span>
          </div>
          <div style={{ fontFamily: SANS, fontSize: 12.5, color: C.ink2, marginTop: 10, lineHeight: 1.55 }}>
            {sk.desc}
          </div>
        </div>
      </div>
    );
  });
}

// ── JSON 载荷 ───────────────────────────────────────────────────────────────
function PayloadPanel({ T }) {
  const enter = seg(T, 0.0, 0.6, MOTION.enter);
  const exitK = seg(T, 6.35, 7.3, MOTION.draw);
  const s = mix(1, 0.1, exitK);
  const opacity = enter * (1 - seg(T, 6.8, 7.3));
  const qHl = seg(T, 1.35, 1.65, MOTION.draw);              // query 行高亮
  return (
    <div data-role="payload" style={{
      position: 'absolute', left: PANEL.x, top: PANEL.y, width: PANEL.w, height: PANEL.h,
      transform: `translateY(${14 * (1 - enter) - 26 * exitK}px) scale(${s})`,
      transformOrigin: '490px 278px', opacity,
      background: C.codeBg, borderRadius: 10,
      boxShadow: '0 14px 40px oklch(24% 0.02 260 / 0.22)',
      fontFamily: MONO, fontSize: 13.5, lineHeight: PANEL.lh + 'px',
      padding: `${PANEL.padY}px ${PANEL.padX}px`, boxSizing: 'border-box',
    }}>
      {/* query 行高亮条 */}
      <div style={{
        position: 'absolute', left: 16, top: Q_LINE.y - PANEL.y, width: 560, height: 30,
        background: 'oklch(45% 0.09 250 / 0.22)', borderLeft: '3px solid ' + C.accent,
        borderRadius: 4, opacity: qHl,
      }} />
      {/* criteria 落线闪光 */}
      {SKILLS.map((sk, j) => {
        const flash = seg(T, LAND(j), LAND(j) + 0.18) * (1 - seg(T, LAND(j) + 0.4, LAND(j) + 0.85));
        return (
          <div key={sk.key} style={{
            position: 'absolute', left: 16, top: CRIT_LINE(j).y - PANEL.y, width: 720, height: 30,
            background: 'oklch(83% 0.09 145 / 0.16)', borderRadius: 4, opacity: flash,
          }} />
        );
      })}
      {/* 逃生舱行琥珀左边条 */}
      <div style={{
        position: 'absolute', left: 16, top: lineY(15) - PANEL.y - 2, width: 3, height: 28,
        background: C.codeEsc, borderRadius: 2, opacity: seg(T, ESC_POP, ESC_POP + 0.3),
      }} />
      {PAYLOAD_LINES.map((segs, i) => (
        <div key={i} style={{ opacity: lineOpacity(T, i), whiteSpace: 'pre' }}>
          {segs.map((sg, k) => (
            <span key={k} style={{
              color: sg.c, fontStyle: sg.it ? 'italic' : 'normal',
              fontWeight: sg.b ? 700 : 400,
            }}>{sg.t}</span>
          ))}
          {i === 6 && qHl > 0.5 && (
            <span style={{ color: C.codeCmt, fontStyle: 'italic' }}>{'   // ← 输入的 query 原样落在这里'}</span>
          )}
          {i === 15 && (
            <span style={{
              marginLeft: 12, fontSize: 10.5, fontWeight: 700, color: C.codeEsc,
              border: '1px solid ' + C.codeEsc, borderRadius: 4, padding: '1px 6px',
              opacity: seg(T, ESC_POP + 0.15, ESC_POP + 0.45),
            }}>逃生舱</span>
          )}
        </div>
      ))}
    </div>
  );
}

// ── 决策模型节点 ────────────────────────────────────────────────────────────
function ModelNode({ T }) {
  const pop = seg(T, 6.9, 7.45, MOTION.pop);
  const out = seg(T, 10.45, 11.1, MOTION.draw);
  const s = mix(0.7, 1, pop) * mix(1, 0.85, out);
  const opacity = Math.min(1, pop * 1.4) * (1 - out);
  return (
    <React.Fragment>
      {/* 脉冲环 */}
      {[0, 1, 2].map((k) => {
        const ph = T < 7.6 || T > 10.6 ? -1 : (((T - 7.6) * 0.9 - k * 0.34) % 1 + 1) % 1;
        if (ph < 0) return null;
        return (
          <div key={k} style={{
            position: 'absolute', left: NODE.x, top: NODE.y, width: NODE.w, height: NODE.h,
            border: '2px solid ' + C.accent, borderRadius: 16, boxSizing: 'border-box',
            transform: `scale(${1 + ph * 0.4})`, transformOrigin: 'center',
            opacity: 0.4 * (1 - ph) * (1 - out),
          }} />
        );
      })}
      <div data-role="node" style={{
        position: 'absolute', left: NODE.x, top: NODE.y, width: NODE.w, height: NODE.h,
        transform: `scale(${s})`, transformOrigin: 'center', opacity,
        background: C.ink, borderRadius: 14, boxSizing: 'border-box',
        boxShadow: '0 10px 30px oklch(24% 0.02 260 / 0.2)',
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      }}>
        <div style={{ fontFamily: MONO, fontSize: 21, fontWeight: 700, color: C.paper }}>决策模型</div>
        <div style={{
          fontFamily: MONO, fontSize: 11.5, color: C.paper, opacity: 0.62,
          letterSpacing: '0.08em', marginTop: 10,
        }}>POST /decide · 一次调用 · 返回归一化概率分布</div>
      </div>
      {/* 应答芯片 */}
      {(() => {
        const k = seg(T, 10.05, 10.95, MOTION.draw);
        const o = seg(T, 10.05, 10.35) * (1 - seg(T, 10.6, 10.95));
        if (k <= 0 || o <= 0) return null;
        return (
          <div style={{
            position: 'absolute', left: mix(NODE.x + NODE.w - 40, 950, k), top: mix(NODE.y + 60, 140, k),
            transform: `scale(${mix(0.5, 0.95, k)})`, transformOrigin: 'left top', opacity: o,
            background: C.white, border: '1px solid ' + C.line, borderRadius: 8,
            padding: '8px 14px', fontFamily: MONO, fontSize: 13, color: C.ink,
            boxShadow: '0 6px 18px oklch(24% 0.02 260 / 0.1)',
          }}>
            {'{ "pick": '}<span style={{ color: C.select, fontWeight: 700 }}>"s0"</span>{' · p '}
            <span style={{ color: C.select, fontWeight: 700 }}>0.91</span>{' }'}
          </div>
        );
      })()}
    </React.Fragment>
  );
}

// ── 输出概率条 ──────────────────────────────────────────────────────────────
function BarsCard({ T }) {
  const enter = seg(T, 10.85, 11.5, MOTION.enter);
  return (
    <div data-role="bars" style={{
      position: 'absolute', left: BARS.x, top: BARS.y + 22 * (1 - enter),
      width: BARS.w, height: BARS.h, opacity: enter,
      background: C.white, border: '1px solid ' + C.line, borderRadius: 12,
      boxSizing: 'border-box', boxShadow: '0 10px 30px oklch(24% 0.02 260 / 0.08)',
    }}>
      <div style={{
        position: 'absolute', left: 36, top: 34, fontFamily: SERIF, fontSize: 15,
        fontWeight: 700, letterSpacing: '0.12em', color: C.ink2,
        opacity: seg(T, 11.1, 11.6),
      }}>输出 · 每个选项一个校准概率</div>

      {/* 胜出行高亮 */}
      <div style={{
        position: 'absolute', left: 26, top: ROW_Y(0) - BARS.y - 10, width: BARS.w - 52, height: 42,
        background: 'oklch(55% 0.13 155 / 0.07)', borderLeft: '3px solid ' + C.select,
        borderRadius: 4, opacity: seg(T, 13.25, 13.7),
      }} />

      {RESULTS.map((r, i) => {
        const start = 11.9 + i * 0.32, dur = 1.05;
        const k = seg(T, start, start + dur, MOTION.draw);
        const tagK = seg(T, start + dur + 0.08, start + dur + 0.34, MOTION.pop);
        const y = ROW_Y(i) - BARS.y;
        return (
          <div key={r.name}>
            <div style={{
              position: 'absolute', left: 36, top: y - 6, fontFamily: MONO, fontSize: 13.5,
              fontWeight: 700, color: r.color === C.skip ? C.ink : r.color,
              opacity: seg(T, 11.5 + i * 0.1, 11.9 + i * 0.1),
            }}>{r.name}</div>
            <div style={{ position: 'absolute', left: 36, top: y + 16, opacity: tagK }}>
              <Tag text={r.verdict} color={r.color} />
            </div>
            <div style={{
              position: 'absolute', left: TRACK.x - BARS.x, top: y, width: TRACK.w, height: TRACK.h,
              background: C.paper, border: '1px solid ' + C.line, borderRadius: 4, overflow: 'hidden',
              opacity: seg(T, 11.55 + i * 0.1, 11.85 + i * 0.1),
            }}>
              <div style={{
                width: k * r.p * TRACK.w, height: '100%', background: r.color, borderRadius: 3,
              }} />
            </div>
            <div style={{
              position: 'absolute', left: TRACK.x - BARS.x + TRACK.w + 12, top: y - 1, width: 56,
              textAlign: 'right', fontFamily: MONO, fontSize: 14, fontVariantNumeric: 'tabular-nums',
              color: i === 0 ? C.select : C.ink2, fontWeight: i === 0 ? 700 : 400,
              opacity: seg(T, 11.55 + i * 0.1, 11.85 + i * 0.1),
            }}>{(k * r.p).toFixed(2)}</div>
          </div>
        );
      })}

      {/* margin 括号：冠军条末端 ↔ 亚军条末端 */}
      {(() => {
        const x1 = TRACK.x - BARS.x + 0.05 * TRACK.w + 10;
        const x2 = TRACK.x - BARS.x + 0.91 * TRACK.w - 8;
        const y = ROW_Y(1) - BARS.y - 20;
        const k = seg(T, 14.9, 15.6, MOTION.draw);
        return (
          <React.Fragment>
            <div style={{
              position: 'absolute', left: x1, top: y, width: x2 - x1, height: 2,
              background: C.ink2, transform: `scaleX(${k})`, transformOrigin: 'left',
            }} />
            {[x1, x2 - 2].map((x, i2) => (
              <div key={i2} style={{
                position: 'absolute', left: x, top: y - 4, width: 2, height: 10,
                background: C.ink2, opacity: k,
              }} />
            ))}
            <div style={{
              position: 'absolute', left: x1, top: y - 26, width: x2 - x1, textAlign: 'center',
              fontFamily: MONO, fontSize: 12.5, color: C.ink,
              opacity: seg(T, 15.4, 15.85),
            }}>margin = 0.91 − 0.05 = <b>0.86</b></div>
          </React.Fragment>
        );
      })()}

      {/* meta 行 */}
      <div style={{
        position: 'absolute', left: 36, right: 36, top: 452 - BARS.y, height: 1,
        background: C.line, opacity: seg(T, 14.2, 14.6),
      }} />
      {[
        { at: 14.5, y: 476, parts: [{ t: 'none_probability', c: C.review, b: 1 }, { t: ' = 0.02', c: C.ink, b: 1 }, { t: '  — 「不需要任何技能」的概率（逃生舱出口）', c: C.ink2 }] },
        { at: 15.9, y: 512, parts: [{ t: 'margin', c: C.ink, b: 1 }, { t: ' = 0.86', c: C.ink, b: 1 }, { t: '  — 冠亚军概率差，不足则降级 review', c: C.ink2 }] },
        { at: 16.4, y: 548, parts: [{ t: 'usage', c: C.ink, b: 1 }, { t: ' · input_tokens / cost_usd', c: C.ink, b: 1 }, { t: '  — 逐次透明回显', c: C.ink2 }] },
      ].map((m, i) => (
        <div key={i} style={{
          position: 'absolute', left: 36, top: m.y - BARS.y, fontFamily: MONO, fontSize: 13,
          opacity: seg(T, m.at, m.at + 0.4, MOTION.enter),
          transform: `translateY(${8 * (1 - seg(T, m.at, m.at + 0.4, MOTION.enter))}px)`,
        }}>
          {m.parts.map((pp, k2) => (
            <span key={k2} style={{ color: pp.c, fontWeight: pp.b ? 700 : 400 }}>{pp.t}</span>
          ))}
        </div>
      ))}
    </div>
  );
}

// ── 每秒在导出根上写当前时间戳（便于逐帧评论）───────────────────────────────
function ScreenLabel() {
  const c = useComposition();
  const sec = Math.floor(c.T);
  React.useEffect(() => {
    const el = document.querySelector('[data-om-exportable-video-with-duration-secs]');
    if (el) el.setAttribute('data-screen-label', sec + 's');
  }, [sec]);
  return null;
}

// ── 整片 ────────────────────────────────────────────────────────────────────
function Piece() {
  const { T, CUES } = useComposition();
  const pill = {
    display: 'inline-block', background: 'oklch(100% 0 0 / 0.82)',
    border: '1px solid ' + C.line, borderRadius: 999, padding: '8px 22px',
    boxShadow: '0 4px 14px oklch(24% 0.02 260 / 0.08)',
  };
  const cap = (txt) => <span style={pill}>{txt}</span>;
  return (
    <div style={{ position: 'absolute', inset: 0, fontFamily: SANS }}>
      <ScreenLabel />
      <StageLabels T={T} CUES={CUES} />
      <PayloadPanel T={T} />
      <HeroQueryCard T={T} />
      <SkillCards T={T} />
      <ModelNode T={T} />
      <BarsCard T={T} />
      <Captions
        items={[
          { at: 0.9, until: 2.9, text: cap('组装：候选技能集不进 state —— 内联为选项，全局比较无损失') },
          { at: 3.3, until: 6.3, text: cap('wire 标识用位置 key（s0 / s1 / s2），调用方的 name 原样透传') },
          { at: 7.7, until: 10.3, text: cap('推理：一次 /decide 调用，模型同时比较全部选项') },
          { at: 12.1, until: 14.4, text: cap('输出：每个选项一个校准概率与 verdict') },
          { at: 15.1, until: 17.5, text: cap('margin 是第二判据：冠亚军差距不足即降级 review') },
        ]}
        style={{
          font: '500 20px ' + SANS, color: C.ink, textShadow: 'none', bottom: '5%',
        }}
      />
    </div>
  );
}

function App() {
  const [t, setTweak] = useTweaks(window.TWEAK_DEFAULTS);
  return (
    <React.Fragment>
      <CompositionStage
        width={1280} height={720}
        scenes={window.OM_SCENES}
        playback={window.OM_PLAYBACK}
        bg={C.paper}
      >
        <Piece />
      </CompositionStage>
      <TweaksPanel title="skill_search motion">
        <TweakSection label="Motion" />
        <TweakToggle label="Motion editor" value={t.motionEditor} onChange={(v) => setTweak('motionEditor', v)} />
      </TweaksPanel>
    </React.Fragment>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
