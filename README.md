# DSH on Windows: stop routing hooks through pwsh

[中文](README.zh.md) | English

DeepSeek Harness wraps every spawned command as
`pwsh -NoLogo -NoProfile -NonInteractive -Command "<command>"`.
Hooks and most tool calls are just an executable plus arguments — the shell wrapper is pure overhead.
On Windows it can add several seconds to every hook.

This plugin hands such commands straight to the OS and falls back to the original path for everything else.

## Result

Same machine, same DSH version, same sequence of tool calls:

| | Hook time |
|---|---|
| Stock DSH | ~4300 ms |
| With plugin | ~240 ms |

Hooks run on every tool call, so in practice this saves about four seconds per operation.

These numbers were measured in one environment only: Windows 11, DSH 0.2.1-rc.2, single machine. Yours may differ — see "Measure first".

## Measure first

Before installing, check how slow you actually are. Run something that triggers a hook (ask the assistant to edit a file, for example) and time it from request to response.

- Under half a second: the plugin won't help you, skip it.
- A second or more: worth trying.

The plugin is conservative. It only goes direct when the first token is an absolute path and no shell metacharacter is present; everything else returns to the original pwsh path, so command semantics don't change.

## Install (plugin, recommended)

The plugin never touches `app.asar`, so DSH upgrades won't wipe it.

1. Copy `plugin/index.js` to:

```
%USERPROFILE%\.dsh\profiles\desktop\plugins\dsh-pwsh-fast\index.js
```

2. Append this to `%USERPROFILE%\.dsh\profiles\desktop\cordis.patch.yml`, replacing the username:

```yaml
- insert:
    - id: "dsh-pwsh-fast"
      name: "file:///C:/Users/<your-name>/.dsh/profiles/desktop/plugins/dsh-pwsh-fast/index.js"
```

3. Restart DSH.

To confirm it's active, set `DEBUG` to `true` in `index.js` and restart. After a few tool calls, `pwsh-fast.log` will appear next to the file, listing which commands went direct and which fell back.

## Uninstall

Remove that block from `cordis.patch.yml` and restart. `app.asar` was never modified.

## Alternative: patch app.asar

If your DSH version doesn't expose `PwshLocalExecutor` (the plugin exits silently), use `install.py` instead.

```
python install.py            # check only
python install.py install    # back up, then patch
python install.py revert     # restore from backup
```

What the script does:

1. Reads the SHA-256 of the target file. Proceeds only if it matches the stock build.
2. Exits immediately if the file already matches the patched fingerprint.
3. Refuses to run if neither matches — that means DSH was upgraded.
4. Copies the whole `app.asar` to `app.asar.bak-<timestamp>` first.
5. Writes the new file as `app.asar.new`, validates the header structure, then swaps it in.

DSH must be fully closed before patching; the file is locked while it runs.

## What it changes

The plugin and `install.py` implement the same logic.

File: `dsh/node_modules/@deepseek-ai/dsh-pwsh-local/lib/index.js`

`PwshLocalExecutor.prototype.argv(spec)` originally returned the pwsh-wrapped argv unconditionally. It now calls `splitSimpleCommand(command)` first: if the command splits into argv, it goes direct; otherwise it falls back unchanged.

When it refuses to go direct:

- Pipes, redirection, `;`, `$`, backticks, `#`
- Newlines or carriage returns
- Smart quotes (PowerShell treats them as quotes, the tokenizer doesn't)
- Unbalanced quotes
- First token is not an absolute path
- Target is `.cmd` / `.bat` / `.ps1` (these need a shell)
- Any argument contains `--%`

Timeouts, buffers and exit-code handling are untouched.

## Known limits

- **Exit codes**: direct execution preserves the original code; through pwsh, 2 and 7 are rewritten to 1. DSH only treats `2` as a block signal and `0` as "parse JSON". Across 16,041 hook invocations on the test machine, `exit 2` never occurred.
- **Large output**: 2,000 lines came back complete, tail marker intact.
- **Version pinning**: `install.py` identifies versions by file fingerprint and refuses to run after a DSH upgrade. The plugin isn't version-bound and keeps working.

## License

MIT, see `LICENSE`. The `@deepseek-ai/dsh-pwsh-local` package it modifies is MIT as well (Copyright (c) 2026 DeepSeek); the full notice is included in `LICENSE`.
