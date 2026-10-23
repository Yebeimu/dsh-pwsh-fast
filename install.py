# -*- coding: utf-8 -*-
"""DSH pwsh 钩子直连补丁。

问题：DSH 起子进程时会把命令包成
`pwsh -NoLogo -NoProfile -NonInteractive -Command "<命令>"`。
钩子命令通常只是「可执行文件 + 参数」，不需要 shell。
这层包装在 Windows 上每次要多花几秒。

改法：只动一个文件
  dsh/node_modules/@deepseek-ai/dsh-pwsh-local/lib/index.js
两处：新增 __splitSimpleCommand()，在 argv(spec) 开头调用它。
其余命令照旧走 pwsh，行为不变。

用法：
  python install.py                     检查，不改动
  python install.py install             备份后安装
  python install.py revert              从备份恢复
  python install.py --asar <路径> ...   指定 app.asar

退出码：0 成功或无需改动，1 出错，2 版本不匹配，3 需要人工确认。
"""

import argparse
import glob
import hashlib
import json
import os
import struct
import subprocess
import sys
import time

TARGET = "/dsh/node_modules/@deepseek-ai/dsh-pwsh-local/lib/index.js"

# 官方 0.2.1-rc.2 里这个文件的指纹
ORIGINAL_SHA256 = "8b7b57eb7f6c597caa5ee72e4dfd88cec7b5ac51e450ed3521b6b6b29306b88e"
# 打过本补丁之后的指纹
PATCHED_SHA256 = "a57a1c1a75d42073e50a2ee7c33876862d7e4eda324d0152a48474bcbbaead69"

ANCHOR_HEAD = '/** Default SIGTERM→SIGKILL grace period (the `graceMs` config). */'
ANCHOR_ARGV = '\targv(spec) {\n\t\treturn ['
ANCHOR_ARGV_NEW = (
    '\targv(spec) {\n'
    '\t\tconst __simple = __splitSimpleCommand(spec.command);\n'
    '\t\tif (__simple !== void 0) return __simple;\n'
    '\t\treturn ['
)

FUNC = r'''/**
* Split a command that is already just "executable + arguments" into argv.
* Returns undefined when the command needs a real shell: any metacharacter,
* an unbalanced quote, or a bare command name that would need PATH lookup.
* A shell-free command avoids the pwsh startup cost on every hook.
*/
function __splitSimpleCommand(command) {
	if (typeof command !== "string") return void 0;
	/* Newlines separate statements in PowerShell; a line-based split would
	merge them into arguments. */
	if (command.includes("\n") || command.includes("\r")) return void 0;
	const argv = [];
	let cur = "";
	let quoted = "";
	for (let i = 0; i < command.length; i++) {
		const ch = command[i];
		if (quoted !== "") {
			if (ch === quoted) quoted = "";
			else cur += ch;
			continue;
		}
		/* Smart quotes are quotes to PowerShell but not to this scanner. */
		if (ch === "\u201C" || ch === "\u201D" || ch === "\u2018" || ch === "\u2019") return void 0;
		if (ch === "\"" || ch === "'") { quoted = ch; continue; }
		if ("|&;<>()$`#".includes(ch)) return void 0;
		if (ch === " " || ch === "\t") {
			if (cur !== "") { argv.push(cur); cur = ""; }
			continue;
		}
		cur += ch;
	}
	if (quoted !== "") return void 0;
	if (cur !== "") argv.push(cur);
	if (argv.length === 0) return void 0;
	const head = argv[0];
	if (!/^[A-Za-z]:[\\/]/.test(head) && !head.includes("\\") && !head.includes("/")) return void 0;
	/* .cmd/.bat need cmd.exe, .ps1 needs a shell: CreateProcessW cannot start them. */
	if (/\.(cmd|bat|ps1|cmd.exe|bat.exe)$/i.test(head)) return void 0;
	/* `--%` tells PowerShell to stop interpreting; direct exec would hand it to the target. */
	for (const a of argv) if (a.includes("--%")) return void 0;
	return argv;
}
'''


def read_asar(path):
    with open(path, "rb") as f:
        _first, pickle, _hsize, jsize = struct.unpack("<IIII", f.read(16))
        header = json.loads(f.read(jsize).decode("utf-8"))
        f.seek(8 + pickle)
        data = f.read()
    return header, data


def node_of(header, target):
    node = header
    for part in target.strip("/").split("/"):
        node = node["files"][part]
    return node


def write_asar(dest, header, data):
    raw = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with open(dest, "wb") as f:
        f.write(struct.pack("<IIII", 4, 8 + len(raw), 4 + len(raw), len(raw)))
        f.write(raw)
        f.write(data)


def verify_header(path):
    with open(path, "rb") as f:
        _size, header_size = struct.unpack("<II", f.read(8))
        buf = f.read(header_size)
    if len(buf) != header_size:
        return False, "头部长度不符"
    payload, slen = struct.unpack("<II", buf[:8])
    if payload != header_size - 4:
        return False, "payloadSize 不符"
    if slen != payload - 4:
        return False, "strLen 不符"
    try:
        json.loads(buf[8:8 + slen].decode("utf-8"))
    except Exception as exc:
        return False, "JSON 解析失败：%s" % exc
    return True, "header=%d payload=%d strLen=%d" % (header_size, payload, slen)


def dsh_running():
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq DeepSeek Harness.exe", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        return "DeepSeek Harness.exe" in (out.stdout or "")
    except Exception:
        return False


def find_asar(explicit):
    if explicit:
        return explicit
    for pat in (r"D:\DSH\resources\app.asar", r"C:\Program Files\DeepSeek Harness\resources\app.asar"):
        if os.path.exists(pat):
            return pat
    hits = glob.glob(os.path.join(os.path.expanduser("~"), "**", "resources", "app.asar"), recursive=True)
    return hits[0] if hits else None


def classify(body_bytes):
    digest = hashlib.sha256(body_bytes).hexdigest()
    if digest == ORIGINAL_SHA256:
        return "original", digest
    if digest == PATCHED_SHA256:
        return "patched", digest
    return "unknown", digest


def build_patched(body):
    if body.count(ANCHOR_HEAD) != 1:
        raise RuntimeError("插入点不唯一（%d 处），版本可能不同" % body.count(ANCHOR_HEAD))
    if body.count(ANCHOR_ARGV) != 1:
        raise RuntimeError("argv(spec) 锚点不唯一（%d 处），版本可能不同" % body.count(ANCHOR_ARGV))
    out = body.replace(ANCHOR_HEAD, FUNC + ANCHOR_HEAD)
    out = out.replace(ANCHOR_ARGV, ANCHOR_ARGV_NEW)
    return out


def do_install(asar, apply_changes):
    header, data = read_asar(asar)
    node = node_of(header, TARGET)
    off, size = int(node["offset"]), int(node["size"])
    body = data[off:off + size].decode("utf-8")
    state, digest = classify(body.encode("utf-8"))

    print("asar      %s（%s 字节）" % (asar, format(os.path.getsize(asar), ",")))
    print("目标文件  %s" % TARGET)
    print("当前指纹  %s" % digest)
    print("状态      %s" % {"original": "官方原版", "patched": "已装补丁", "unknown": "未知版本"}[state])

    if state == "patched":
        print("\n已装，无需改动。")
        return 0
    if state == "unknown":
        print("\n这个文件的指纹与已知的两个版本都不一致。")
        print("多半是 DSH 升级了。请等补丁跟进，不要强行安装。")
        return 2

    new_body = build_patched(body)
    new_bytes = new_body.encode("utf-8")
    delta = len(new_bytes) - size
    new_digest = hashlib.sha256(new_bytes).hexdigest()
    print("补丁后    文件 %s 字节（%+d），指纹 %s" % (format(len(new_bytes), ","), delta, new_digest))
    if new_digest != PATCHED_SHA256:
        print("警告：结果指纹与预期不一致，可能是补丁数据过期。")
        return 2

    if not apply_changes:
        print("\n只检查，未改动。要安装请加 install。")
        return 0

    if dsh_running():
        print("\nDSH 正在运行，app.asar 被占用。先完全退出 DSH 再安装。")
        return 3

    new_data = data[:off] + new_bytes + data[off + size:]
    changed = [0]

    def walk(item, path=""):
        if isinstance(item, dict) and "files" in item:
            for key, value in item["files"].items():
                walk(value, path + "/" + key)
        elif isinstance(item, dict) and "offset" in item:
            old_off = int(item["offset"])
            if path == TARGET:
                item["size"] = len(new_bytes)
                if "integrity" in item:
                    item["integrity"]["hash"] = new_digest
                changed[0] += 1
            elif old_off > off:
                item["offset"] = str(old_off + delta)

    walk(header)
    if changed[0] != 1:
        print("\n目录项没有正确更新，放弃。")
        return 1

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = "%s.bak-%s" % (asar, stamp)
    tmp = asar + ".new"

    print("\n备份  %s" % backup)
    try:
        with open(asar, "rb") as src, open(backup, "wb") as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
    except OSError as exc:
        print("备份失败：%s" % exc)
        return 1

    write_asar(tmp, header, new_data)
    ok, detail = verify_header(tmp)
    print("写出  %s（头部校验 %s）" % (tmp, "通过" if ok else "不通过 " + detail))
    if not ok:
        os.remove(tmp)
        return 1

    try:
        os.replace(tmp, asar)
    except OSError as exc:
        print("替换失败：%s" % exc)
        print("备份还在 %s，原文件没动。" % backup)
        if os.path.exists(tmp):
            os.remove(tmp)
        return 1

    print("完成。重启 DSH 后生效。要撤销：python install.py revert")
    return 0


def do_revert(asar):
    cands = sorted(glob.glob(asar + ".bak-*"), key=os.path.getmtime, reverse=True)
    if not cands:
        print("没找到备份（%s.bak-*）。" % asar)
        return 1
    latest = cands[0]
    print("最近的备份  %s（%s 字节）" % (latest, format(os.path.getsize(latest), ",")))
    if dsh_running():
        print("DSH 正在运行。先完全退出 DSH。")
        return 3
    header, data = read_asar(latest)
    node = node_of(header, TARGET)
    body = data[int(node["offset"]):int(node["offset"]) + int(node["size"])].decode("utf-8")
    state, _ = classify(body.encode("utf-8"))
    print("备份里的状态  %s" % {"original": "官方原版", "patched": "已装补丁", "unknown": "未知"}[state])
    try:
        os.replace(latest, asar)
    except OSError as exc:
        print("恢复失败：%s" % exc)
        return 1
    print("已恢复。重启 DSH 生效。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="DSH pwsh 钩子直连补丁")
    ap.add_argument("action", nargs="?", default="check", choices=["check", "install", "revert"])
    ap.add_argument("--asar", help="app.asar 路径，默认自动查找")
    args = ap.parse_args()

    asar = find_asar(args.asar)
    if not asar or not os.path.exists(asar):
        print("找不到 app.asar，用 --asar 指定。")
        return 1

    if args.action == "revert":
        return do_revert(asar)
    return do_install(asar, args.action == "install")


if __name__ == "__main__":
    sys.exit(main())
