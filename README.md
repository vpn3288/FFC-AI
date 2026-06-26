---

## ⚠️ 重要：稳定性修复 / Important: Stability Fix

**如果你遇到以下问题：**
- Claude Code 任务频繁中断
- 任务长时间运行后断开
- 出现 "执行失败" 错误
- 任务卡在 "calling_model" 状态

**快速修复：**

```bash
cd /path/to/FFC-AI
bash scripts/quick-fix-claude-stability.sh
```

这会自动优化配置并重启服务，预计成功率从 40-80% 提升到 70-95%。

**详细信息：** 查看 [CLAUDE_CODE_STABILITY_FIX.md](CLAUDE_CODE_STABILITY_FIX.md)

---
# FFC-AI - 用 Telegram 控制服务器上的 AI 助手

FFC-AI 可以把 Claude Code、Codex、VSCode 和 Telegram 连接起来。装好以后，你在手机 Telegram 里发消息，家里 PVE 虚拟机或 VPS 上的 AI 就能帮你写代码、改文件、运行命令、继续长任务。

这份 README 按新手流程写：先准备东西，再复制命令安装，最后用 Telegram 测试。

---

## 先看这一段

如果你不懂这些词，照着下面理解就够了：

- `服务器`：你的 Debian 12 VPS，或者家里 PVE 里的 Debian 12 虚拟机。
- `root`：服务器最高权限账号。这个项目就是给专用 AI 服务器用的。
- `API key`：AI 服务商给你的密钥，像密码一样保管，不能发到公开群。
- `API 地址/base URL`：第三方代理给你的接口地址。OpenAI 兼容接口通常以 `/v1` 结尾。
- `Telegram bot token`：BotFather 给你的机器人 token，用来让服务器收发 Telegram 消息。
- `CC Switch`：可选的本地图形配置管理器，适合有桌面/远程桌面的机器管理多套 key 和代理。纯 VPS 可以跳过。

新手推荐：先用默认全量安装 `all,telegram`，安装成功后再慢慢调整。

---

## 安装前准备

你需要准备 4 样东西：

1. 一台干净的 `Debian 12` 服务器。
2. 能登录服务器终端，并且能执行 `sudo` 或直接使用 `root`。
3. 一个 Telegram bot token。
4. 至少一组可用的 AI API 配置。

API 配置按工具分两类，不要混用：

| 你要用的工具 | 应该填什么 key | API 地址怎么填 |
| --- | --- | --- |
| `codex` | OpenAI 或 OpenAI 兼容 key | 通常类似 `https://api.example.com/v1` |
| `claude-code` | Anthropic/Claude 或 Claude 兼容 key | 按代理说明填写，通常不强制 `/v1` |
| `vscode` | 这里作为 Claude 后端使用，填 Claude 兼容 key | 同 Claude 代理地址 |

如果你只有第三方 OpenAI 代理，先选 `codex,telegram` 最简单。
如果你只有第三方 Claude 代理，先选 `claude-code,telegram`。

---

## 第 1 步：创建 Telegram Bot

1. 打开 Telegram，搜索 `@BotFather`。
2. 给 BotFather 发送 `/newbot`。
3. 按提示给 bot 起名字。
4. 复制 BotFather 返回的 token，格式大概是：

```text
1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
```

5. 打开你刚创建的 bot，先发一条 `/start`。这一步能让配对更顺利。

---

## 第 2 步：一键安装

在 Debian 12 服务器终端复制下面整行命令运行。不要拆开，不要漏引号。

```bash
sudo bash -c 'set -e; apt-get update; apt-get install -y curl ca-certificates; f=$(mktemp); curl -fsSL https://raw.githubusercontent.com/vpn3288/FFC-AI/main/scripts/bootstrap-debian12.sh -o "$f"; bash "$f"'
```

脚本会自动做这些事：

- 安装系统依赖、Node.js LTS、Claude Code、Codex CLI、VSCode。
- Codex CLI 按 `versions.lock` 安装当前锁定稳定版；如果机器已有旧版 Codex，会自动升级到锁定版本。
- 创建 `/opt/ai-remote-runner`、`/var/lib/ai-remote-runner`、`/srv/ai-workspaces`。
- 用 systemd 启动 runner 和 Telegram bot。
- 引导你输入 API key、API 地址、模型名、Telegram token。
- 可选安装 CC Switch。

安装过程中常见问题这样选：

| 脚本问题 | 新手怎么选 |
| --- | --- |
| `请选择安装模式` | 不懂就直接回车，默认全量安装。想轻量就填 `codex,telegram` |
| `是否安装 CC Switch` | 有桌面/远程桌面就填 `yes`，纯 VPS 就直接回车跳过 |
| `Codex/OpenAI API base URL` | 官方 OpenAI 可留空；第三方代理填它给你的 `/v1` 地址 |
| `OpenAI/Codex API key` | 填 OpenAI 或 OpenAI 兼容 key |
| `Anthropic API base URL` | 官方 Claude 可留空；第三方 Claude 代理按说明填 |
| `Anthropic API key/token` | 填 Claude/Anthropic 兼容 key |
| `Telegram Bot Token` | 粘贴 BotFather 给你的 token |

安装完成后，如果脚本提示保存成功，就进入下一步。

---

## 第 3 步：Telegram 测试

打开你的 Telegram bot，发送：

```text
/ai 状态
```

能看到状态回复，就说明 Telegram 已经接通。

再试一条普通任务：

```text
你好，介绍一下自己
```

如果安装时跳过了 Telegram token，之后可以手动配对：

```bash
cd /root/FFC-AI
sudo bash scripts/pair-telegram.sh --discover-chat-id
```

---

## 日常怎么用

直接发普通消息就是让 AI 做任务：

```text
请查看当前工作区有哪些文件
帮我写一个 Python 脚本，列出当前目录的所有 .py 文件
检查这个项目有没有明显的安装问题
```

常用命令：

```text
/ai 状态          查看系统状态
/ai 帮助          查看所有命令
/ai 功能          查看已安装能力
/ai 配置 查看      查看当前 key/代理/模型是否配置
/ai 提供商 列表    查看 codex、claude-code、vscode 状态
/ai 提供商 使用 codex
/ai 继续          让当前任务继续
/ai 定时继续      查看自动继续设置
/ai 强行停止      停止 runner 已登记启动的任务
```

`/ai 强行停止` 只停止 runner 自己启动并登记的进程，不会按名字乱杀系统里的其它 `codex`、`claude`、`node` 或 `python`。

---

## 第三方 API 怎么填

很多人用的是第三方代理，不是官方 OpenAI/Anthropic。记住一条：`codex` 走 OpenAI 兼容配置，`claude-code` 和 `vscode` 走 Claude/Anthropic 兼容配置。

安装后也可以在 Telegram 里改，不用重新安装。

Codex/OpenAI 兼容配置：

```text
/ai 代理 设置 codex https://你的OpenAI兼容地址/v1
/ai 密钥 设置 codex sk-你的OpenAI兼容key
/ai GPT模型 设置 codex gpt-5.5
/ai 配置 查看 codex
```

Claude Code 配置：

```text
/ai 代理 设置 claude-code https://你的Claude兼容地址
/ai 密钥 设置 claude-code sk-ant-你的Claude兼容key
/ai Claude模型 设置 claude-code claude-opus-4-8
/ai 配置 查看 claude-code
```

VSCode 后端配置：

```text
/ai 代理 设置 vscode https://你的Claude兼容地址
/ai 密钥 设置 vscode sk-ant-你的Claude兼容key
/ai Claude模型 设置 vscode claude-opus-4-8
/ai 配置 查看 vscode
```

如果你把 `sk-ant-` 这种 Claude key 填给 `codex`，runner 会拒绝，防止配错。

Codex 使用第三方 OpenAI 兼容代理时，runner 会写入自定义 `model_provider`，并设置：

```toml
wire_api = "responses"
supports_websockets = false
request_max_retries = 6
stream_max_retries = 10
stream_idle_timeout_ms = 600000
```

这样做是为了避免 Linux 服务环境里部分代理的 websocket/长流连接兼容问题。官方 OpenAI 地址留空时，脚本使用 Codex 官方推荐的 `openai_base_url` 配置。

---

## CC Switch 怎么用

CC Switch 是可选功能，不是必须安装。

适合安装 CC Switch 的情况：

- 你的 Debian 12 有桌面环境或远程桌面。
- 你有多套 Claude/OpenAI 第三方代理。
- 你想用图形界面管理不同 API key、API 地址和模型档案。

可以跳过 CC Switch 的情况：

- 你是纯 VPS/headless 服务器。
- 你只想用 Telegram 远程控制。
- 你只有一套 API key。

手动启用 CC Switch 安装：

```bash
AI_INSTALL_CC_SWITCH=true AI_RUNNER_COMPONENTS=all,telegram sudo -E bash scripts/install-runner.sh
```

Telegram 也支持 CC Switch 兼容命令：

```text
/ai CC Switch 状态
/ai CC Switch 密钥 设置 codex sk-你的OpenAI兼容key
/ai CC Switch 代理 设置 codex https://你的OpenAI兼容地址/v1
/ai CC Switch GPT模型 设置 codex gpt-5.5
/ai CC Switch 密钥 设置 claude-code sk-ant-你的Claude兼容key
/ai CC Switch 代理 设置 claude-code https://你的Claude兼容地址
/ai CC Switch Claude模型 设置 claude-code claude-opus-4-8
```

这些命令不会直接修改 CC Switch 的 SQLite 数据库。它们会写入 Claude Code、Codex、runner 实际读取的 live 配置，并记录同步状态。这样更安全，也不会破坏 AI 软件本身的配置逻辑。

---

## 🎯 不同AI工具选择

下面这些命令适合已经安装过、或者已经在服务器上有 `/root/FFC-AI` 项目目录时使用。先进入项目目录：

```bash
cd /root/FFC-AI
```

需要一台 Debian 12 VM/VPS 同时准备三套工具时，可以直接全量安装：

```bash
AI_RUNNER_COMPONENTS=all,telegram sudo -E bash scripts/install-runner.sh
```

全量安装会全局准备 Claude Code、Codex、VSCode、runner 和 Telegram 服务。runner 仍然一次只使用一个默认 provider，避免同一个任务同时乱跑多套工具；可以在 Telegram 里切换：

```
/ai 提供商 使用 codex
/ai 提供商 使用 claude-code
/ai 提供商 使用 vscode
```

如果你希望单机更轻，也可以只安装其中一种工具：

### Codex（推荐新手）
```bash
AI_RUNNER_COMPONENTS=codex,telegram sudo -E bash scripts/install-runner.sh
```
- 安装最简单。
- 支持 OpenAI 官方 API，也支持第三方 OpenAI 兼容代理。
- 如果只想先跑通 Telegram 远程 AI，优先选这个。

### Claude Code（适合Claude用户）
```bash
AI_RUNNER_COMPONENTS=claude-code,telegram sudo -E bash scripts/install-runner.sh
```
- 脚本会安装 Claude Code CLI。
- 安装时会引导你填写 Claude/Anthropic API key。
- 适合主要使用 Claude 模型的人。

### VSCode（高级用户）
```bash
AI_RUNNER_COMPONENTS=vscode,telegram sudo -E bash scripts/install-runner.sh
```
- 脚本会安装 VSCode/root wrapper。
- 这里默认把 VSCode 当作 Claude 后端使用。
- 适合已经知道自己为什么需要 VSCode 集成的用户。

---

## 🛠️ 常见问题

### ❓ 如何查看服务状态

```bash
sudo systemctl status ai-remote-runner
sudo systemctl status ai-telegram-bot
```

### ❓ 如何查看日志

```bash
sudo journalctl -u ai-telegram-bot -n 50 -f
```

### ❓ Bot不回复消息

1. 检查服务是否运行：
```bash
sudo systemctl status ai-telegram-bot
```

2. 查看日志找错误：
```bash
sudo journalctl -u ai-telegram-bot -n 100 --no-pager
```

3. 确认chat ID配置正确：
```bash
sudo grep TELEGRAM_ALLOWED_CHAT_IDS /var/lib/ai-remote-runner/config.env
```

4. 重启服务：
```bash
sudo systemctl restart ai-telegram-bot
```

### ❓ 安装时 API key 填错了怎么办

不用重装。直接在 Telegram 里改：

```text
/ai 密钥 设置 codex sk-新的OpenAI兼容key
/ai 代理 设置 codex https://新的OpenAI兼容地址/v1
/ai 密钥 设置 claude-code sk-ant-新的Claude兼容key
/ai 代理 设置 claude-code https://新的Claude兼容地址
/ai 配置 查看
```

改完后可以发 `/ai 状态` 或 `/ai 配置 查看` 确认。

### ❓ 如何重新配对

```bash
cd /root/FFC-AI
sudo bash scripts/pair-telegram.sh --discover-chat-id
```

运行后按提示给 bot 发消息，脚本会自动发现你的 chat ID。

### ❓ 网络连不上Telegram API

某些网络环境可能无法直接访问Telegram。如果遇到超时，可以：

1. 检查网络：
```bash
curl -I https://api.telegram.org
```

2. 如果连不上，可以使用代理或更换网络环境

### ❓ 想换其他AI工具

先卸载当前的：
```bash
sudo systemctl stop ai-remote-runner ai-telegram-bot
sudo systemctl disable ai-remote-runner ai-telegram-bot
```

然后重新安装：
```bash
AI_RUNNER_COMPONENTS=claude-code,telegram sudo -E bash scripts/install-runner.sh
```

---

## 📚 进阶功能

### 工作区管理
```
/ai 工作区 列表
/ai 工作区 创建 myproject
/ai 工作区 使用 myproject
```

### 上下文管理
```
/ai 上下文          # 查看当前上下文
/ai 压缩            # 手动压缩上下文
/ai 新对话          # 开始新对话
/ai 开启新对话      # 同上，只有明确发送才更换对话
```

### 权限控制
```
/ai 聊天模式 开启    # 只能聊天
/ai 编辑模式 开启    # 可以编辑文件
/ai 终端模式 开启    # 可以运行命令
/ai 完全访问 开启    # 完全权限（默认）
```

### 直接执行命令
```
/ai shell ls -la
/ai 执行 python3 --version
```

### 长任务控制
```
/ai 继续
/ai 定时继续 设置 300
/ai 定时继续 关闭
/ai 强行停止
```

`/ai 定时继续 设置 300` 会在当前 Telegram chat 每 300 秒发送一次普通提示 `继续`；如果同一个 chat 里已有任务还在跑，会跳过本轮，避免叠加任务。`/ai 强行停止` 只终止 runner 自己登记启动的 provider 或本机命令进程，不会按进程名乱杀系统里的其它 `codex`、`claude`、`node` 或 `python`。

### 配置不同模型
```
/ai 模型 列表 codex
/ai 开源模型 设置 codex gpt-4o-mini
```

### Codex子agent实时状态
Codex运行时会把JSONL事件流转换成Telegram里的实时状态。默认会高亮显示审查者AI、子agent、命令执行、文件修改等状态。
安装脚本默认写入 `CODEX_EXEC_EPHEMERAL=0`，普通 Telegram 消息会继续复用当前 runner 对话；Codex 返回 thread_id 后，runner 会在后续同一对话里使用 `codex exec resume` 续接。只有发送 `/ai 新对话` 或 `/ai 开启新对话` 才更换对话。若 Codex 上下文接近上限，Telegram 会显示警告；若 Codex 退出时仍有工具调用没返回结果，runner 会按“中断”处理，不会误报完成。

### ❓ 遇到 "Reconnecting... 5/5" 错误

如果 Telegram 里看到 `执行出错：Reconnecting... 5/5`，这是 Codex CLI 在 Linux 服务器上使用第三方 OpenAI 兼容代理时，websocket 长连接不稳定导致的。**Windows 环境不出现此问题**，因为网络栈和连接方式不同。

**一键修复：**

```bash
cd /root/FFC-AI
sudo bash scripts/fix-codex-reconnecting.sh
```

修复脚本会自动：
- 检测当前配置
- 配置 Codex 使用 HTTP responses API（而非 websocket）
- 增加重试次数和超时时间
- 验证修复结果

修复完成后重启服务：

```bash
sudo systemctl restart ai-telegram-bot
```

**手动修复方法：**

如果自动脚本不可用，可以手动设置：

```text
/ai 代理 设置 codex https://你的OpenAI兼容地址/v1
/ai 密钥 设置 codex sk-你的OpenAI兼容key
```

然后重新运行安装脚本让它重写配置：

```bash
AI_RUNNER_COMPONENTS=codex,telegram sudo -E bash scripts/install-runner.sh
```

**技术说明：**

第三方 OpenAI 兼容代理的 websocket 实现可能不完整。修复脚本会在 `~/.codex/config.toml` 中创建自定义 model_provider，设置：
- `wire_api = "responses"` - 使用 HTTP 而非 websocket
- `supports_websockets = false`
- `request_max_retries = 6`
- `stream_max_retries = 10`
- `stream_idle_timeout_ms = 600000` (10分钟)

这些配置专门优化了 Linux 服务器环境下第三方代理的稳定性。

```
/ai 子agent状态          # 查看当前是否开启
/ai 子agent状态 开启     # 显示Codex子agent/审查者AI状态
/ai 子agent状态 关闭     # 不再单独高亮子agent，仍保留普通运行状态
/ai jsonl 关闭           # 同上，英文缩写别名
```

### ❓ Claude Code 超时或连接问题

如果使用 Claude Code 时遇到超时、连接中断或 API 错误，特别是使用第三方 Claude 兼容 API 时：

**一键诊断：**

```bash
cd /root/FFC-AI
sudo bash scripts/diagnose-claude-code.sh
```

**一键修复：**

```bash
cd /root/FFC-AI
sudo bash scripts/fix-claude-code-timeout.sh
sudo systemctl restart ai-telegram-bot
```

修复脚本会自动优化第三方 API 配置：
- `requestTimeout: 180000` (3分钟请求超时)
- `streamTimeout: 600000` (10分钟流式超时)
- `maxRetries: 5` (最大重试5次)
- 自动禁用不必要的网络流量

**技术说明：**

第三方 Claude 兼容 API 在 Linux 服务器上可能需要更长的超时时间和更多重试次数。修复脚本会在 `~/.claude/settings.json` 中添加优化配置，确保与 Windows 环境的行为一致。

---

## 🏢 团队使用（Mattermost）

如果需要团队协作，可以部署Mattermost：

```bash
cd /root/FFC-AI
sudo bash scripts/install-communication-vps.sh --domain ai.example.com
```

需要准备：
- 域名（例如 ai.example.com）
- 域名已解析到服务器IP
- 80和443端口开放

安装后查看管理员账号：
```bash
sudo grep MATTERMOST_ADMIN /opt/ffc-ai-mattermost/.env
```

详细Mattermost配置请查看 `docs/` 目录。

---

## 🔒 安全提醒

⚠️ **重要安全说明**

- 这个项目给AI**完全访问权限**，只在专用服务器/虚拟机上使用
- 不要在重要的生产服务器上安装
- 不要把API Key发到公开聊天
- 保护好你的bot token
- 确保只有你的chat ID能控制bot
- 定期备份 `/var/lib/ai-remote-runner` 目录

---

## 📁 重要文件位置

```
/opt/ai-remote-runner              # 程序安装目录
/var/lib/ai-remote-runner          # 配置和状态目录
  ├── config.env                   # 主配置文件（包含密钥）
  └── install-manifest.json        # 安装清单
/srv/ai-workspaces                 # AI工作区
/root/.codex/                      # Codex配置
/root/.claude/                     # Claude配置
```

---

## 🆘 获取帮助

- 查看当前说明：`cat README.md`
- 查看安装设计要求：`cat outputs/DEBIAN12_FULL_ACCESS_TELEGRAM_OPTIMIZATION_GUIDE.md`
- 问题反馈：https://github.com/vpn3288/FFC-AI/issues
- 查看所有脚本：`ls scripts/`

---

## 🔄 更新和卸载

### 更新代码
```bash
cd /root/FFC-AI
git pull
AI_RUNNER_COMPONENTS=all,telegram sudo -E bash scripts/install-runner.sh
```

### 卸载
```bash
cd /root/FFC-AI
sudo bash scripts/rollback-install.sh
```

---

## ✅ 验证安装

运行完整测试：
```bash
cd /root/FFC-AI
sudo bash scripts/validate-core-ready.sh
```

看到 `core_ready=true` 表示安装成功！

---

**开始使用吧！在Telegram给你的bot发送消息试试。** 🎉
