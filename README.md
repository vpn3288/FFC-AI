# FFC-AI - 用手机控制你的AI助手 📱

**5分钟快速开始** | 用Telegram给你的服务器上的Claude/GPT下命令，让AI帮你写代码、改配置、跑脚本。

---

## 🚀 最快开始（推荐新手）

**只需要3步：**

### 第1步：获取Telegram Bot Token（1分钟）

1. 在Telegram搜索 `@BotFather`
2. 发送 `/newbot` 创建机器人
3. 复制BotFather给你的token（类似：`1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`）
4. 给你的新bot发一条消息（例如 `/start`）

### 第2步：在Debian 12服务器上安装（2分钟）

复制这一行到 root 或有 sudo 权限的服务器终端，直接运行。脚本会默认安装 Claude Code、Codex、VSCode、runner 和 Telegram，并引导你选择组件、输入 API 配置和 Telegram token。

```bash
sudo bash -c 'set -e; apt-get update; apt-get install -y curl ca-certificates; f=$(mktemp); curl -fsSL https://raw.githubusercontent.com/vpn3288/FFC-AI/main/scripts/bootstrap-debian12.sh -o "$f"; bash "$f"'
```

安装过程会自动：
- 安装 Debian 12 依赖、Node.js 24.x LTS（已有 Node 22/24 等合格偶数稳定版会直接复用）、Claude Code、Codex CLI、VSCode
- 配置 root/full-access 运行环境和 systemd 服务
- 写入不同 provider 对应的 API key、base URL、模型配置
- 配对 Telegram（如果安装时输入了 bot token）

### 第3步：在Telegram测试

在Telegram给bot发：

```
/ai 状态
```

如果安装时跳过了 Telegram token，再手动配对：

```bash
cd /root/FFC-AI
sudo bash scripts/pair-telegram.sh --discover-chat-id
```

**完成！** 之后就可以直接在Telegram里给服务器上的AI下任务。

---

## 💬 基本使用

安装好后，在Telegram直接跟AI对话：

```
你好，介绍一下自己

请查看/root目录下有什么文件

帮我写一个Python脚本，列出当前目录的所有.py文件
```

或使用命令：

```
/ai 状态          # 查看系统状态
/ai 帮助          # 查看所有命令
/ai 功能          # 查看功能列表
/ai 子agent状态   # 查看Codex子agent状态显示开关
/ai 定时继续      # 查看当前聊天的自动继续设置
/ai 强行停止      # 终止runner已登记的活动任务
```

---

## 🔧 使用第三方API（可选）

如果你用的是第三方OpenAI兼容API或Anthropic兼容API，推荐直接用上面的 bootstrap 安装；它会分别询问 Codex/OpenAI 和 Claude/Anthropic 的配置，不会把两种 key 混用。

### 安装后修改API配置

也可以在Telegram里动态修改：

```
/ai 代理 设置 codex https://你的API地址/v1
/ai 密钥 设置 codex sk-你的密钥
/ai 开源模型 设置 codex gpt-4o
/ai 配置 查看 codex
```

Claude Code 和 VSCode 使用 Anthropic/Claude 配置面：

```
/ai 代理 设置 claude-code https://你的Claude代理地址
/ai 密钥 设置 claude-code sk-ant-你的密钥
/ai Claude模型 设置 claude-code claude-opus-4-8
/ai 代理 设置 vscode https://你的Claude代理地址
/ai 密钥 设置 vscode sk-ant-你的密钥
/ai GPT模型 设置 vscode gpt-5.5
```

---

## 🎯 不同AI工具选择

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
- ✅ 安装最简单
- ✅ 支持OpenAI和兼容API
- ✅ 稳定可靠

### Claude Code（适合Claude用户）
```bash
AI_RUNNER_COMPONENTS=claude-code,telegram sudo -E bash scripts/install-runner.sh
```
- 需要Claude Code CLI
- 需要提前配置ANTHROPIC_API_KEY
- 支持Claude模型

### VSCode（高级用户）
```bash
AI_RUNNER_COMPONENTS=vscode,telegram sudo -E bash scripts/install-runner.sh
```
- 自动安装VSCode
- 支持Claude后端
- 适合需要VSCode集成的场景

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

### ❓ 如何重新配对

```bash
cd /root/FFC-AI
sudo bash scripts/pair-telegram.sh --telegram-id 你的ID
```

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
安装脚本默认开启 `CODEX_EXEC_EPHEMERAL=1`，由runner保存长期对话记忆，避免Codex CLI自己把历史工具输出越滚越大。若Codex上下文接近上限，Telegram会显示警告；若Codex退出时仍有工具调用没返回结果，runner会按“中断”处理，不会误报完成。

```
/ai 子agent状态          # 查看当前是否开启
/ai 子agent状态 开启     # 显示Codex子agent/审查者AI状态
/ai 子agent状态 关闭     # 不再单独高亮子agent，仍保留普通运行状态
/ai jsonl 关闭           # 同上，英文缩写别名
```

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

- 查看详细文档：`cat README.md`（项目原版README）
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
