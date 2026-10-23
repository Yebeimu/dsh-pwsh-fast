// dsh-pwsh-fast：让不需要 shell 的命令直连系统创建进程，跳过 pwsh 包装。
//
// DSH 起子进程时把命令包成 pwsh -NoLogo -NoProfile -NonInteractive -Command "<命令>"。
// 钩子和多数工具调用只是「可执行文件 + 参数」，这层包装在 Windows 上每次要多花几秒。
// 本插件把 PwshLocalExecutor.prototype.argv 换成先试直连的版本，其余情况原样回退。
//
// 不该直连的情况一律返回 undefined 交回原实现：管道与重定向、$、反引号、#、换行、
// 弯引号、引号不配对、首 token 不是绝对路径、.cmd/.bat/.ps1、参数里带 --%。
//
// 调试时把 DEBUG 改成 true，每次判定会追加一行到 pwsh-fast.log（同目录）。

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PwshLocalExecutor } from '@deepseek-ai/dsh-pwsh-local';

const DEBUG = false;
const LOG = path.join(path.dirname(fileURLToPath(import.meta.url)), 'pwsh-fast.log');

function note(message) {
  if (!DEBUG) return;
  try {
    fs.appendFileSync(LOG, `${new Date().toISOString()} ${message}\n`);
  } catch {}
}

function splitSimpleCommand(command) {
  if (typeof command !== 'string') return undefined;
  if (command.includes('\n') || command.includes('\r')) return undefined;
  const argv = [];
  let cur = '';
  let quoted = '';
  for (let i = 0; i < command.length; i++) {
    const ch = command[i];
    if (quoted !== '') {
      if (ch === quoted) quoted = '';
      else cur += ch;
      continue;
    }
    if (ch === '\u201C' || ch === '\u201D' || ch === '\u2018' || ch === '\u2019') return undefined;
    if (ch === '"' || ch === "'") { quoted = ch; continue; }
    if ('|&;<>()$`#'.includes(ch)) return undefined;
    if (ch === ' ' || ch === '\t') {
      if (cur !== '') { argv.push(cur); cur = ''; }
      continue;
    }
    cur += ch;
  }
  if (quoted !== '') return undefined;
  if (cur !== '') argv.push(cur);
  if (argv.length === 0) return undefined;
  const head = argv[0];
  if (!/^[A-Za-z]:[\\/]/.test(head) && !head.includes('\\') && !head.includes('/')) return undefined;
  if (/\.(cmd|bat|ps1|cmd\.exe|bat\.exe)$/i.test(head)) return undefined;
  for (const a of argv) if (a.includes('--%')) return undefined;
  return argv;
}

export default {
  name: 'dsh-pwsh-fast',
  apply() {
    const proto = PwshLocalExecutor && PwshLocalExecutor.prototype;
    if (!proto || typeof proto.argv !== 'function') {
      note('跳过：拿不到 PwshLocalExecutor.prototype.argv');
      return;
    }
    if (proto.argv.__dshPwshFast) {
      note('已经在位，不重复替换');
      return;
    }
    const original = proto.argv;
    const patched = function argv(spec) {
      const simple = splitSimpleCommand(spec && spec.command);
      if (simple !== undefined) {
        note('直连 ' + String(spec.command).slice(0, 120));
        return simple;
      }
      note('回退 ' + String(spec && spec.command).slice(0, 120));
      return original.call(this, spec);
    };
    patched.__dshPwshFast = true;
    proto.argv = patched;
    note('已替换 PwshLocalExecutor.prototype.argv');
  },
};
