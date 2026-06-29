#!/usr/bin/env node
// =============================================================
//  ローカル動作確認ハーネス (B-1)
//  n8n の agent-runner.json と同じロジックを再現し、
//  CEO -> 企画 -> 本文 -> デザイン -> 予約 -> (承認) -> 投稿 -> 分析
//  のパイプラインを1回流して handoff の連結を検証する。
//
//  ANTHROPIC_API_KEY があれば実APIを呼ぶ。無ければ役割別モックで動く。
//  使い方: node scripts/run-local.mjs  [--goal "..."]
// =============================================================
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const API_KEY = process.env.ANTHROPIC_API_KEY || '';
const DEFAULT_MODEL = process.env.DEFAULT_MODEL || 'claude-opus-4-8';
const APPROVAL = (process.env.HUMAN_APPROVAL_REQUIRED || 'true') === 'true';
const MODE = API_KEY ? 'LIVE (Anthropic API)' : 'MOCK (no API key)';

const log = (...a) => console.log(...a);
const ok = (m) => log(`  \x1b[32m✓\x1b[0m ${m}`);
const step = (m) => log(`\n\x1b[1m▶ ${m}\x1b[0m`);

// --- agent-runner と同じ: prompt.md + config を読み込む -------------
function loadAgent(agentId) {
  const dir = join(ROOT, 'agents', agentId);
  const system = readFileSync(join(dir, 'prompt.md'), 'utf8');
  let cfg = {};
  try { cfg = JSON.parse(readFileSync(join(dir, 'agent.config.json'), 'utf8')); } catch {}
  const model = (cfg.model || '').replace('${DEFAULT_MODEL}', DEFAULT_MODEL) || DEFAULT_MODEL;
  return { system, model, maxTokens: cfg.maxTokens || 2000, temperature: cfg.temperature ?? 0.7 };
}

// --- agent-runner と同じ: 出力テキストからJSONを取り出す ------------
function parseOutput(text) {
  const cleaned = text.replace(/^```json\s*/i, '').replace(/^```\s*/, '').replace(/```\s*$/, '').trim();
  try { return JSON.parse(cleaned); } catch (e) { return { result: { raw: text }, parseError: String(e) }; }
}

// --- モデル呼び出し（実API or モック） ------------------------------
async function callModel({ system, model, maxTokens, temperature }, userPayload, agentId) {
  if (!API_KEY) return JSON.stringify(mockFor(agentId, userPayload));
  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'x-api-key': API_KEY,
      'anthropic-version': '2023-06-01',
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      model, max_tokens: maxTokens, temperature, system,
      messages: [{ role: 'user', content: JSON.stringify(userPayload, null, 2) }],
    }),
  });
  if (!res.ok) throw new Error(`Anthropic API ${res.status}: ${await res.text()}`);
  const data = await res.json();
  return (data.content?.[0]?.text) || '';
}

// --- 役割別モック（output スキーマに準拠した形を返す） --------------
function mockFor(agentId, input) {
  const ctx = input.context || {};
  switch (agentId) {
    case 'ceo':
      return { plan: ['企画→本文→デザイン→予約→投稿→分析'],
        assignments: [{ agentId: 'content-planner', task: '新商品告知の企画を3案', context: ctx.constraints || {} }],
        needsHumanApproval: APPROVAL };
    case 'content-planner':
      return { result: { ideas: [
        { title: '新商品ローンチ', angle: '日常の困りごと解決', targetChannel: 'x-main', goal: '認知' },
        { title: '開発の裏側', angle: '舞台裏ストーリー', targetChannel: 'x-main', goal: 'エンゲージ' },
      ] }, handoffTo: 'copywriter' };
    case 'copywriter':
      return { result: { copies: [
        { channel: 'x-main', body: '【新登場】毎日の◯◯がもっとラクに。', hashtags: ['#新商品', '#YourBrand'], cta: '詳しくはプロフィールから' },
      ] }, handoffTo: 'designer' };
    case 'designer':
      return { result: { assets: [
        { channel: 'x-main', type: 'image', prompt: 'minimal product hero shot, soft light', url: 'mock://asset/1.png' },
      ] }, handoffTo: 'scheduler' };
    case 'scheduler':
      return { result: { scheduled: [
        { channel: 'x-main', scheduledAt: '2026-07-01T12:00:00+09:00', payload: { copies: ctx.post?.copies, assets: ctx.post?.assets } },
      ] }, handoffTo: 'publisher' };
    case 'publisher':
      if (!ctx.approved) return { result: { published: [], note: '未承認のため投稿中止' }, handoffTo: 'ceo' };
      return { result: { published: [
        { channel: 'x-main', postId: 'mock-12345', url: 'https://x.com/mock/status/12345', status: 'ok' },
      ] }, handoffTo: 'analyst' };
    case 'analyst':
      return { result: { metrics: { impressions: 4200, engagementRate: 0.058, conversions: 17 },
        insights: ['投稿時刻12時が好反応。次回は画像比率4:5を試す'] }, handoffTo: 'ceo' };
    default:
      return { result: { raw: 'mock' }, handoffTo: 'ceo' };
  }
}

// --- 1エージェント実行（loadAgent -> callModel -> parse） -----------
async function runAgent(agentId, task, context) {
  const cfg = loadAgent(agentId);
  ok(`load ${agentId}: model=${cfg.model} (prompt ${cfg.system.length} chars)`);
  const text = await callModel(cfg, { task, context }, agentId);
  const parsed = parseOutput(text);
  if (parsed.parseError) throw new Error(`${agentId} のJSONパース失敗: ${parsed.parseError}`);
  return parsed;
}

// --- パイプライン本体 ------------------------------------------------
async function main() {
  const goalArg = process.argv.indexOf('--goal');
  const goal = goalArg > -1 ? process.argv[goalArg + 1] : '新商品の告知キャンペーンを今週中に回して';

  log(`\x1b[1mAI SNS Company — ローカル動作確認\x1b[0m`);
  log(`mode: ${MODE} | model: ${DEFAULT_MODEL} | approval: ${APPROVAL}`);
  log(`goal: ${goal}`);

  step('1. CEO がゴールを分解');
  const ceo = await runAgent('ceo', goal, { constraints: { channels: ['x-main'] } });
  ok(`assignments: ${JSON.stringify(ceo.assignments?.map(a => a.agentId))}`);

  step('2. Content Pipeline を実行');
  const planner = await runAgent('content-planner', '企画を立てる', { constraints: {} });
  ok(`ideas: ${planner.result.ideas.length}件 → handoff: ${planner.handoffTo}`);

  const copy = await runAgent('copywriter', '本文を書く', { idea: planner.result.ideas[0], ideas: planner.result.ideas });
  ok(`copies: ${copy.result.copies.length}件 → handoff: ${copy.handoffTo}`);

  const designer = await runAgent('designer', 'ビジュアル作成', { copies: copy.result.copies });
  ok(`assets: ${designer.result.assets.length}件 → handoff: ${designer.handoffTo}`);

  const scheduler = await runAgent('scheduler', '日時割り当て', { post: { copies: copy.result.copies, assets: designer.result.assets } });
  ok(`scheduled: ${scheduler.result.scheduled.length}件 → handoff: ${scheduler.handoffTo}`);

  step('3. 承認ゲート');
  if (APPROVAL) {
    log('  HUMAN_APPROVAL_REQUIRED=true → 本番のn8nではここでWaitし承認待ち。検証では承認済みとして続行。');
  } else {
    log('  HUMAN_APPROVAL_REQUIRED=false → そのまま投稿。');
  }

  step('4. 投稿 → 分析');
  const publisher = await runAgent('publisher', '投稿実行', { scheduled: scheduler.result.scheduled, approved: true });
  ok(`published: ${JSON.stringify(publisher.result.published)} → handoff: ${publisher.handoffTo}`);

  const analyst = await runAgent('analyst', '分析', { published: publisher.result.published, period: {} });
  ok(`metrics: ${JSON.stringify(analyst.result.metrics)}`);
  ok(`insights: ${JSON.stringify(analyst.result.insights)}`);

  step('✅ パイプライン完走（CEO→企画→本文→デザイン→予約→投稿→分析）');
  log(`\nmode was: ${MODE}`);
  if (!API_KEY) log('（ANTHROPIC_API_KEY を設定すると同じ流れを実モデルで実行します）');
}

main().catch((e) => { console.error(`\n\x1b[31m✗ 失敗:\x1b[0m ${e.message}`); process.exit(1); });
