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
if (invalidArg || !model || !effort || !promptArg || !['low', 'medium', 'high'].includes(effort)
  || !['plan', 'builder', 'critic', 'finalizer'].includes(explicitStage)) {
  console.error('用法：claude.mjs --model <模型> --effort <low|medium|high> --stage <阶段> <Ralph 提示词>');
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

const stage = explicitStage;
const { task, subtask } = detectTaskIds(fullPrompt);
const child = spawn(
  process.env.RALPH_CLAUDE_BIN ?? 'claude',
  [
    '--print', '--model', model, '--effort', effort,
    '--permission-mode', 'bypassPermissions', '--dangerously-skip-permissions',
    '--tools', 'Read', 'Glob', 'Grep', 'Edit', 'Write', 'Bash',
    '--output-format', 'stream-json', '--verbose', '--no-session-persistence',
  ],
  { stdio: ['pipe', 'pipe', 'inherit'], env: { ...process.env, PWD: process.cwd() } },
);
const startedAt = Date.now();
child.stdin.on('error', () => {});
child.stdin.end(fullPrompt);

let tokens = null;
let failed = false;
const output = createInterface({ input: child.stdout });
output.on('line', (line) => {
  let event;
  try {
    event = JSON.parse(line);
  } catch {
    process.stdout.write(`${line}\n`);
    return;
  }
  if (event.type === 'assistant') {
    for (const content of event.message?.content ?? []) {
      if (content.type === 'text') process.stdout.write(content.text ?? '');
      if (content.type === 'tool_use') process.stdout.write(`\n[${content.name ?? 'tool'}]\n`);
    }
  }
  if (event.type === 'result') {
    if (event.is_error) failed = true;
    if (event.usage) tokens = event.usage;
  }
});

child.on('error', (error) => {
  failed = true;
  console.error(`Claude Code 启动失败：${error.message}`);
});

child.on('close', async (code, signal) => {
  const event = await waitForEmittedEvent(cursor);
  if (!event) console.error('未检测到 Ralph 交接事件，本次执行判为失败。');
  const ok = Boolean(!failed && event && !code);
  await appendRunLog({
    runner: 'claude', stage, model, effort, task, subtask,
    duration_ms: Date.now() - startedAt, tokens, exit_code: code ?? (signal ? 130 : 0),
    status: ok ? 'success' : 'failed', event,
  });
  process.stdout.write('\n');
  process.exitCode = code || (signal ? 130 : 0) || (!ok ? 1 : 0);
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => child.kill(signal));
}
