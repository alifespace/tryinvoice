import { execFileSync } from 'node:child_process';
import { appendFile, lstat, mkdir, readFile, realpath } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

export async function resolvePrompt(input) {
  const prefix = 'Please read and execute the task in ';
  if (!input.startsWith(prefix)) return input;

  const file = input.slice(prefix.length);
  const roots = [os.tmpdir(), '/tmp', '/var/tmp'];
  if (process.platform === 'darwin') {
    roots.push(execFileSync('/usr/bin/getconf', ['DARWIN_USER_TEMP_DIR'], { encoding: 'utf8' }).trim());
  }

  const temporaryRoots = await Promise.all(roots.map((root) => realpath(root)));
  const stat = await lstat(file);
  const resolved = await realpath(file);
  if (!path.isAbsolute(file) || !path.basename(file).startsWith('.tmp')) {
    throw new Error('Ralph 临时任务文件名无效');
  }
  if (!temporaryRoots.includes(path.dirname(resolved))) {
    throw new Error('Ralph 任务文件不在系统临时目录内');
  }
  if (stat.isSymbolicLink() || !stat.isFile() || stat.size > 8 * 1024 * 1024) {
    throw new Error('Ralph 任务文件类型或大小无效');
  }
  if (process.getuid && stat.uid !== process.getuid()) {
    throw new Error('Ralph 任务文件所有者不匹配');
  }

  const prompt = await readFile(resolved, 'utf8');
  if (!prompt.trim()) throw new Error('Ralph 临时任务文件为空');
  return prompt;
}

export async function eventCursor(cwd = process.cwd()) {
  const marker = path.join(cwd, '.ralph/current-events');
  let target;
  try {
    target = (await readFile(marker, 'utf8')).trim();
  } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  }

  const file = path.resolve(cwd, target);
  if (path.dirname(file) !== path.join(cwd, '.ralph') || !/^events.*\.jsonl$/.test(path.basename(file))) {
    throw new Error('Ralph 事件文件路径无效');
  }

  let content = '';
  try {
    content = await readFile(file, 'utf8');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  return { file, content };
}

const HANDOFF_TOPICS = new Set([
  'work.start',
  'spec.ready',
  'build.done',
  'review.accepted',
  'review.rejected',
  'task.replan',
  'task.done',
  'LOOP_COMPLETE',
  'loop.cancel',
]);

export async function waitForEmittedEvent(cursor, { timeoutMs = 3000, intervalMs = 100 } = {}) {
  if (!cursor) return 'untracked';
  const deadline = Date.now() + timeoutMs;
  while (Date.now() <= deadline) {
    try {
      const content = await readFile(cursor.file, 'utf8');
      if (content.startsWith(cursor.content)) {
        const emitted = content
          .slice(cursor.content.length)
          .split('\n')
          .map((line) => {
            try {
              const topic = JSON.parse(line).topic;
              return HANDOFF_TOPICS.has(topic) ? topic : null;
            } catch {
              return null;
            }
          })
          .find(Boolean);
        if (emitted) return emitted;
      }
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  return null;
}

export async function appendRunLog(record, cwd = process.cwd()) {
  try {
    const directory = path.join(cwd, '.ralph');
    await mkdir(directory, { recursive: true });
    await appendFile(
      path.join(directory, 'observability.jsonl'),
      `${JSON.stringify({ schema_version: 1, timestamp: new Date().toISOString(), ...record })}\n`,
      { encoding: 'utf8', mode: 0o600 },
    );
  } catch {
    // 观测日志是非阻塞的；落盘失败不得改变 Ralph 控制平面结果。
  }
}

export function detectTaskIds(prompt) {
  const subtask = prompt.match(/\b(M\d+\.\d+-S\d+)\b/)?.[1] ?? null;
  const explicitTask = prompt.match(/"current_task"\s*:\s*"(M\d+\.\d+)(?:-[^"]+)?"/)?.[1]
    ?? prompt.match(/(?:当前\s*Task|current\s+Task|task_id)\s*[:：=]\s*`?(M\d+\.\d+)/i)?.[1]
    ?? null;
  const taskIds = [...new Set(prompt.match(/\bM\d+\.\d+\b/g) ?? [])];
  const task = subtask?.split('-S')[0] ?? explicitTask ?? (taskIds.length === 1 ? taskIds[0] : null);
  return { task, subtask };
}
