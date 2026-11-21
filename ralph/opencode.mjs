#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { appendRunLog, detectTaskIds, eventCursor, resolvePrompt, waitForEmittedEvent } from './backend-common.mjs';

const argv = process.argv.slice(2);
const promptArg = argv.pop();
let model = '';
let effort = '';
let explicitStage = process.env.RALPH_STAGE || '';
let invalidArg = false;
for (let i = 0; i < argv.length; i += 1) {
  if (argv[i] === '--model' && argv[i + 1]) model = argv[++i];
  else if (argv[i] === '--effort' && argv[i + 1]) effort = argv[++i];
  else if (argv[i] === '--stage' && argv[i + 1]) explicitStage = argv[++i];
  else invalidArg = true;
}
if (invalidArg || !model || !promptArg || (effort && !['low', 'medium', 'high', 'xhigh'].includes(effort))
  || !['plan', 'builder', 'critic', 'finalizer'].includes(explicitStage)) {
  console.error('用法：opencode.mjs --model <模型> [--effort <low|medium|high|xhigh>] --stage <阶段> <Ralph 提示词>');
  process.exit(1);
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

const modelVariant = effort ? `${model}#${effort}` : model;
const stage = explicitStage;
const { task, subtask } = detectTaskIds(fullPrompt);
const providerOverride = model.startsWith('openai/')
  ? JSON.stringify({ providers: { openai: { package: '@ai-sdk/openai' } } })
  : null;
const child = spawn(
  process.env.RALPH_OPENCODE_BIN ?? 'opencode',
  ['run', '--standalone', '--archived', '--format', 'json', '--model', modelVariant],
  {
    stdio: ['pipe', 'pipe', 'inherit'],
    env: {
      ...process.env,
      PWD: process.cwd(),
      ...(providerOverride ? { OPENCODE_CONFIG_CONTENT: providerOverride } : {}),
    },
  },
);
const startedAt = Date.now();
child.stdin.on('error', () => {});
child.stdin.end(fullPrompt);

let failed = false;
let timedOut = false;
let tokens = null;
const idleTimeoutMs = Number(process.env.RALPH_OPENCODE_IDLE_TIMEOUT_MS ?? 300_000);
let idleTimer = setTimeout(onIdleTimeout, idleTimeoutMs);
function resetIdleTimer() {
  clearTimeout(idleTimer);
  idleTimer = setTimeout(onIdleTimeout, idleTimeoutMs);
}
function onIdleTimeout() {
  timedOut = true;
  failed = true;
  console.error(`\nOpenCode 超过 ${Math.round(idleTimeoutMs / 1000)} 秒无输出，终止本次执行。`);
  child.kill('SIGTERM');
  setTimeout(() => child.kill('SIGKILL'), 5000).unref();
}

const output = createInterface({ input: child.stdout });
output.on('line', (line) => {
  resetIdleTimer();
  let event;
  try {
    event = JSON.parse(line);
  } catch {
    process.stdout.write(`${line}\n`);
    return;
  }
  if (event.type === 'step_finish' && event.part?.tokens) tokens = event.part.tokens;
  if (event.type === 'error') {
    failed = true;
    console.error('OpenCode 模型请求失败。');
  }
  if (event.type === 'text') process.stdout.write(event.part?.text ?? '');
  if (event.type === 'tool_use') {
    process.stdout.write(`\n[${event.part?.tool ?? 'tool'}] ${event.part?.state?.status ?? 'unknown'}\n`);
  }
});

child.on('error', (error) => {
  clearTimeout(idleTimer);
  failed = true;
  console.error(`OpenCode 启动失败：${error.message}`);
});

child.on('close', async (code, signal) => {
  clearTimeout(idleTimer);
  const event = await waitForEmittedEvent(cursor);
  if (!event) console.error('未检测到 Ralph 交接事件，本次执行判为失败。');
  const ok = Boolean(!failed && event && !code);
  await appendRunLog({
    runner: 'opencode', stage, model, effort: effort || null, task, subtask,
    duration_ms: Date.now() - startedAt, tokens, exit_code: code ?? (signal ? 130 : 0),
    status: ok ? 'success' : 'failed', event,
    error_reason: ok ? null : (timedOut ? 'idle-timeout' : !event ? 'no-handoff' : `exit-${code ?? signal ?? 'unknown'}`),
  });
  process.stdout.write('\n');
  process.exitCode = code || (signal ? 130 : 0) || (!ok ? 1 : 0);
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => child.kill(signal));
}
