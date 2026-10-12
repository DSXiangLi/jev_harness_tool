#!/usr/bin/env node
// record.mjs — 逐帧 seek + 裁剪截图，输出 PNG 帧序列供 ffmpeg 合成 GIF
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync, rmSync } from 'node:fs';

const SHELL = process.env.HOME + '/.cache/puppeteer/chrome-headless-shell/linux_arm-153.0.8010.36/chrome-headless-shell-linux-arm64/chrome-headless-shell';
const URL = 'http://localhost:8377/reference_verify_motion/index.html';
const OUT = '/tmp/rvm_frames';
const FPS = 13;
const DUR = 24;
const PORT = 9337;
const SCALE = 2; // 2x supersampling：舞台实际渲染 2560x1440，ffmpeg 再缩到 1280，文字更锐

rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });

// 窗口 (1280x764)*SCALE = 舞台 720p*SCALE + 播放条 44*SCALE，svg 缩放恰好为 SCALE
const chrome = spawn(SHELL, ['--headless', '--disable-gpu', '--no-sandbox', `--remote-debugging-port=${PORT}`, `--window-size=${1280 * SCALE},${764 * SCALE}`, 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
async function getWsUrl() {
  for (let i = 0; i < 40; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = list.find(t => t.type === 'page');
      if (page) return page.webSocketDebuggerUrl;
    } catch {}
    await sleep(250);
  }
  throw new Error('chrome not ready');
}
const ws = new WebSocket(await getWsUrl());
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let mid = 0; const pending = new Map(); const errors = [];
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  if (m.method === 'Runtime.exceptionThrown') errors.push(JSON.stringify(m.params.exceptionDetails).slice(0, 300));
};
const send = (method, params = {}) => new Promise((res) => { const id = ++mid; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });

await send('Runtime.enable'); await send('Page.enable');
await send('Page.navigate', { url: URL });
await sleep(3500);

// 确认 svg 精确位于 (0,0) 1280x720*SCALE
const r = await send('Runtime.evaluate', {
  expression: `JSON.stringify(document.querySelector('[data-om-exportable-video-with-duration-secs]').getBoundingClientRect())`,
  returnByValue: true,
});
const rect = JSON.parse(r.result.result.value);
console.log('stage rect:', JSON.stringify(rect));
if (Math.abs(rect.x) > 1 || Math.abs(rect.y) > 1 || Math.abs(rect.width - 1280 * SCALE) > 2) {
  console.log('WARN: stage not at expected position/scale');
}
const clip = { x: rect.x, y: rect.y, width: rect.width, height: rect.height, scale: 1 };

const frames = Math.round(DUR * FPS); // 234
const t0 = Date.now();
for (let i = 0; i < frames; i++) {
  const t = Math.min(i / FPS, DUR - 1e-4);
  await send('Runtime.evaluate', { expression: `document.querySelector('[data-om-exportable-video-with-duration-secs]').dispatchEvent(new CustomEvent('data-om-seek-to-time-frame',{detail:{time:${t},sync:true}}))` });
  const shot = await send('Page.captureScreenshot', { format: 'png', clip });
  writeFileSync(`${OUT}/frame_${String(i).padStart(4, '0')}.png`, Buffer.from(shot.result.data, 'base64'));
  if (i % 40 === 0) console.log(`frame ${i}/${frames}  t=${t.toFixed(2)}s  ${((Date.now() - t0) / 1000).toFixed(1)}s elapsed`);
}
console.log(`done: ${frames} frames in ${((Date.now() - t0) / 1000).toFixed(1)}s; errors:`, errors.length ? errors : '(none)');
ws.close(); chrome.kill(); process.exit(0);
