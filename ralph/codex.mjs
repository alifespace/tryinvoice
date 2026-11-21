#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { appendRunLog, detectTaskIds, eventCursor, resolvePrompt, waitForEmittedEvent } from './backend-common.mjs';

const rawArgs = process.argv.slice(2).filter((arg) =>
  !['--yolo', '--dangerously-bypass-approvals-and-sandbox', '--full-auto'].includes(arg));
const promptArg = rawArgs.pop();
if (!promptArg) {
  console.error('用法：codex.mjs [Codex 参数] --stage <阶段> <Ralph 提示词>');
  process.exit(1);
}

let explicitStage = process.env.RALPH_STAGE || '';
let model = 'gpt-6.1-sol';
const codexArgs = [];
for (let i = 0; i < rawArgs.length; i += 1) {
  if (rawArgs[i] === '--stage' && rawArgs[i + 1]) {
    explicitStage = rawArgs[++i];
    continue;
  }
  if ((rawArgs[i] === '-m' || rawArgs[i] === '--model') && rawArgs[i + 1]) {
    model = rawArgs[i + 1];
  }
  codexArgs.push(rawArgs[i]);
}

let fullPrompt;
let cursor;
try {
  fullPrompt = await resolvePrompt(promptArg);
  cursor = await eventCursor();
} catch (error) {
  console.error(`无法读取 Ralph 任务或事件文件：${error.message}`);
  process.exit(1);
}

const stage = explicitStage;
if (!['plan', 'builder', 'critic', 'finalizer'].includes(stage)) {
  console.error('必须通过 --stage 指定 plan、builder、critic 或 finalizer。');
  process.exit(1);
}
const { task, subtask } = detectTaskIds(fullPrompt);
const startedAt = Date.now();
const child = spawn(
  process.env.RALPH_CODEX_BIN ?? 'codex',
  ['exec', '--full-auto', '--json', ...codexArgs, fullPrompt],
  { stdio: ['ignore', 'pipe', 'inherit'], env: { ...process.env, PWD: process.cwd() } },
);

child.stdout.setEncoding('utf8');
child.stdout.on('data', (chunk) => {
  for (const line of chunk.split('\n')) {
    if (!line.trim()) continue;
    try {
      const event = JSON.parse(line);
      if (event.type === 'item.completed' && event.item?.type === 'agent_message' && event.item?.text) {
        process.stdout.write(event.item.text);
      } else if (event.type === 'item.created' && event.item?.type === 'message' && event.item?.text) {
        process.stdout.write(event.item.text);
      } else if (event.type === 'text') {
        process.stdout.write(event.text ?? '');
      }
    } catch {}
  }
});

child.on('error', (error) => {
  console.error(`Codex CLI 启动失败：${error.message}`);
  process.exitCode = 1;
});

child.on('close', async (code, signal) => {
  const event = await waitForEmittedEvent(cursor);
  if (!event) console.error('未检测到 Ralph 交接事件，本次执行判为失败。');
  const ok = Boolean(event && !code);
  await appendRunLog({
    runner: 'codex', stage, model, effort: null, task, subtask,
    duration_ms: Date.now() - startedAt, tokens: null, exit_code: code ?? (signal ? 130 : 0),
    status: ok ? 'success' : 'failed', event,
  });
  process.stdout.write('\n');
  process.exitCode = code || (signal ? 130 : 0) || (!event ? 1 : 0);
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => child.kill(signal));
}
