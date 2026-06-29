#!/usr/bin/env node
// =============================================================
//  接続チェック (preflight)
//  config/channels.yaml を読み、各チャンネルが「運用可能」か判定する。
//  - enabled: true か
//  - credentialsEnv の環境変数が .env / 環境に揃っているか
//  運用開始前にこれを実行し、すべて READY なら投稿パイプラインを回せる。
//
//  使い方: node scripts/check-connections.mjs
//          （.env を読ませるなら: node --env-file=.env scripts/check-connections.mjs）
// =============================================================
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

// --- 依存ゼロの簡易YAMLパーサ（channels.yaml の構造に特化） ----------
function parseChannels(text) {
  const channels = [];
  let cur = null, listKey = null;
  for (const raw of text.split('\n')) {
    // タブ正規化＋インラインコメント(' #...')除去（値に # を含まない前提）
    const line = raw.replace(/\t/g, '  ').replace(/\s+#.*$/, '');
    if (/^\s*#/.test(raw) || !line.trim()) continue;
    const mNew = line.match(/^\s*-\s+id:\s*(.+?)\s*$/);
    if (mNew) { cur = { id: mNew[1].trim(), credentialsEnv: [] }; channels.push(cur); listKey = null; continue; }
    if (!cur) continue;
    const mList = line.match(/^\s*-\s+(.+?)\s*$/);
    if (mList && listKey) { cur[listKey].push(mList[1].trim()); continue; }
    const mKV = line.match(/^\s*([a-zA-Z0-9_]+):\s*(.*)$/);
    if (mKV) {
      const key = mKV[1], val = mKV[2].trim();
      if (val === '') { if (key === 'credentialsEnv') { listKey = key; cur[key] = []; } else listKey = null; }
      else { listKey = null; cur[key] = val.replace(/^["']|["']$/g, ''); }
    }
  }
  return channels;
}

const yaml = readFileSync(join(ROOT, 'config/channels.yaml'), 'utf8');
const channels = parseChannels(yaml);

const C = { g: '\x1b[32m', r: '\x1b[31m', y: '\x1b[33m', d: '\x1b[2m', b: '\x1b[1m', x: '\x1b[0m' };
console.log(`${C.b}AI SNS Company — 接続チェック (preflight)${C.x}`);
console.log(`${C.d}channels: ${channels.length} 件${C.x}\n`);

let readyCount = 0, enabledCount = 0;
for (const ch of channels) {
  const enabled = String(ch.enabled) === 'true';
  const envs = ch.credentialsEnv || [];
  const missing = envs.filter((e) => !process.env[e]);
  const ready = enabled && missing.length === 0;
  if (enabled) enabledCount++;
  if (ready) readyCount++;

  const badge = !enabled ? `${C.d}○ DISABLED${C.x}`
    : ready ? `${C.g}● READY${C.x}`
    : `${C.y}▲ NEEDS SETUP${C.x}`;
  console.log(`${C.b}${ch.id}${C.x} (${ch.platform}) ${ch.handle || ''}  ${badge}`);
  for (const e of envs) {
    const ok = !!process.env[e];
    console.log(`   ${ok ? C.g + '✓' : C.r + '✗'}${C.x} ${e}${ok ? '' : `  ${C.d}← .env に未設定${C.x}`}`);
  }
  if (ch.n8nCredentialType) console.log(`   ${C.d}n8n credential: ${ch.n8nCredentialType}${C.x}`);
  if (ch.publishWorkflow) console.log(`   ${C.d}publisher: ${ch.publishWorkflow}${C.x}`);
  console.log('');
}

// --- 共通基盤（モデルAPI）の確認 -----------------------------------
const coreOk = !!process.env.ANTHROPIC_API_KEY;
console.log(`${C.b}コア基盤${C.x}`);
console.log(`   ${coreOk ? C.g + '✓' : C.r + '✗'}${C.x} ANTHROPIC_API_KEY${coreOk ? '' : `  ${C.d}← AIエージェント実行に必須${C.x}`}`);
console.log('');

// --- サマリ ----------------------------------------------------------
console.log(`${C.b}結果:${C.x} ${readyCount}/${enabledCount} の有効チャンネルが運用可能` +
  (channels.length - enabledCount > 0 ? `（${channels.length - enabledCount} 件は無効）` : ''));
if (readyCount > 0 && coreOk) {
  console.log(`${C.g}▶ 運用可能。content-pipeline を回せます。${C.x}`);
  process.exit(0);
} else {
  console.log(`${C.y}▶ 未接続の項目を .env に設定し、enabled: true にしてください（docs/go-live.md）。${C.x}`);
  process.exit(1);
}
