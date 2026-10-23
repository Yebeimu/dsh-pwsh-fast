# DSH on Windows：让钩子不再绕 pwsh

[English](README.md) | 中文

DeepSeek Harness 起子进程时，会把命令包成
`pwsh -NoLogo -NoProfile -NonInteractive -Command "<命令>"`。
钩子和多数工具调用其实只是「可执行文件 + 参数」，这层 shell 包装纯属浪费。
在 Windows 上，它能让每次钩子多花几秒。

这个插件在命令不需要 shell 时直接交给系统创建进程，其余情况原样退回。

## 效果

同一台机器、同一个 DSH 版本、同样一串工具调用：

| | 钩子耗时 |
|---|---|
| 官方原版 | 约 4300 ms |
| 装插件后 | 约 240 ms |

钩子每次工具调用都要跑一遍，所以体感差别是每次操作省四秒多。

这两个数只在一种环境测过：Windows 11、DSH 0.2.1-rc.2、单机。你的机器可能不一样，见下面的「先量一下」。

## 先量一下

装之前先看看你现在有多慢。让它跑一个会触发钩子的操作（比如让助手改一个文件），掐一下从发出到返回的秒数。

- 本来就在半秒内：这个插件对你没用，别装。
- 一两秒以上：值得试。

插件本身是保守的——只有「首 token 是绝对路径、没有 shell 元字符」才直连，其余一律退回原来的 pwsh 路径，命令语义不变。

## 安装（推荐：插件）

插件不改 `app.asar`，DSH 升级也不失效。

1. 把 `plugin/index.js` 放到：

```
%USERPROFILE%\.dsh\profiles\desktop\plugins\dsh-pwsh-fast\index.js
```

2. 在 `%USERPROFILE%\.dsh\profiles\desktop\cordis.patch.yml` 末尾加一段（路径里的用户名换成你的）：

```yaml
- insert:
    - id: "dsh-pwsh-fast"
      name: "file:///C:/Users/<你的用户名>/.dsh/profiles/desktop/plugins/dsh-pwsh-fast/index.js"
```

3. 重启 DSH。

想确认有没有生效，把 `index.js` 里的 `DEBUG` 改成 `true`，重启后跑几个工具调用，同目录会出现 `pwsh-fast.log`，里面会写哪些命令走了直连、哪些退回。

## 卸载

从 `cordis.patch.yml` 删掉那一段，重启，就回到原样。`app.asar` 从头到尾没被改过。

## 备选：改 app.asar

如果你的 DSH 版本拿不到 `PwshLocalExecutor`（插件启动时会静默跳过），可以改用 `install.py` 直接改 `app.asar`。

```
python install.py            # 只看，不改
python install.py install    # 备份后安装
python install.py revert     # 从备份恢复
```

脚本会做这些：

1. 读目标文件的 SHA-256。与官方原版一致才继续；
2. 与「已装补丁」的指纹一致就直接退出；
3. 与两个都不一致，说明 DSH 版本变了，拒绝执行；
4. 装之前把整个 `app.asar` 复制成 `app.asar.bak-<时间戳>`；
5. 新文件先写成 `app.asar.new`，校验头部结构，通过才替换。

改 `app.asar` 前必须先完全退出 DSH，文件被占用时无法替换。

## 它做了什么

插件和 `install.py` 改的是同一处逻辑。

文件：`dsh/node_modules/@deepseek-ai/dsh-pwsh-local/lib/index.js`

内容：`PwshLocalExecutor.prototype.argv(spec)` 原本无条件返回 pwsh 包装的 argv。
现在先调一次 `splitSimpleCommand(command)`，切成 argv 就直连；切不了就原样回退。

切不了的情况：

- 管道、重定向、`;`、`$`、反引号、`#`
- 换行或回车
- 弯引号（PowerShell 认、切词器不认）
- 引号不配对
- 首 token 不是绝对路径
- 目标是 `.cmd` / `.bat` / `.ps1`（这些必须经 shell）
- 参数里带 `--%`

没有改超时、缓冲区、退出码处理。

## 已知边界

- **退出码**：直连保留原退出码；经 pwsh 包装时 2 和 7 会被转成 1。DSH 只把 `2` 当 block 语义、`0` 当解析 JSON。查过 16041 次钩子调用，`exit 2` 一次都没出现。
- **大输出**：2000 行完整返回，尾部标记在。
- **版本绑定**：`install.py` 按文件指纹认版本，DSH 升级后指纹会变，脚本会拒绝执行。插件不绑版本，升级后照常工作。

## 许可

MIT，见 `LICENSE`。插件和脚本修改的 `@deepseek-ai/dsh-pwsh-local` 同样是 MIT（Copyright (c) 2026 DeepSeek），完整声明一并附在 `LICENSE` 里。
