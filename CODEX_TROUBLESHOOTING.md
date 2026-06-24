# Codex "Reconnecting... 5/5" 故障排除指南

## 问题症状

在 Telegram 中使用 Codex 时，看到错误信息：

```
执行出错：Reconnecting... 5/5
```

或类似的 websocket/stream 连接失败错误。

## 问题原因

### 根本原因

**Codex CLI 在 Linux 服务器环境下，使用第三方 OpenAI 兼容代理时，websocket 长连接可能不稳定。**

具体技术细节：

1. **Codex CLI 默认使用 websocket** 进行流式通信
2. **第三方代理的 websocket 实现**可能不完整或不稳定
3. **Linux 服务器环境**的网络栈、超时设置与 Windows 桌面环境不同
4. Codex 会自动重试 5 次，全部失败后返回 "Reconnecting... 5/5" 错误

### 为什么 Windows 不出现

- Windows 桌面环境的网络栈和连接管理不同
- 本地运行时网络延迟更低
- 防火墙和代理配置可能更宽松
- 某些代理对桌面客户端做了特殊优化

## 快速修复

### 方法 1：使用自动修复脚本（推荐）

```bash
cd /root/FFC-AI
sudo bash scripts/diagnose-codex.sh      # 先诊断
sudo bash scripts/fix-codex-reconnecting.sh  # 再修复
sudo systemctl restart ai-telegram-bot   # 重启服务
```

修复脚本会自动：
- ✅ 备份当前配置
- ✅ 检测当前 base_url
- ✅ 配置使用 HTTP responses API（而非 websocket）
- ✅ 增加重试次数和超时时间
- ✅ 验证配置正确性

### 方法 2：通过 Telegram 命令修复

```text
/ai 代理 设置 codex https://你的OpenAI兼容地址/v1
/ai 密钥 设置 codex sk-你的OpenAI兼容key
/ai 配置 查看 codex
```

然后重新运行安装脚本：

```bash
cd /root/FFC-AI
AI_RUNNER_COMPONENTS=codex,telegram sudo -E bash scripts/install-runner.sh
```

### 方法 3：手动编辑配置文件

编辑 `~/.codex/config.toml`：

```toml
model_provider = "ffc_openai_compat"
model = "gpt-5.5"
review_model = "gpt-5.5"
model_reasoning_effort = "xhigh"
approval_policy = "never"
sandbox_mode = "danger-full-access"

[shell_environment_policy]
inherit = "all"

[sandbox_workspace_write]
network_access = true

[features]
goals = true

[model_providers.ffc_openai_compat]
name = "OpenAI-compatible proxy"
base_url = "https://你的代理地址/v1"
wire_api = "responses"
env_key = "OPENAI_API_KEY"
supports_websockets = false
request_max_retries = 6
stream_max_retries = 10
stream_idle_timeout_ms = 600000
```

关键配置项说明：
- `wire_api = "responses"` - 使用 HTTP 而非 websocket
- `supports_websockets = false` - 明确禁用 websocket
- `request_max_retries = 6` - 增加请求重试次数
- `stream_max_retries = 10` - 增加流式重试次数
- `stream_idle_timeout_ms = 600000` - 10分钟超时（而非默认的更短时间）

## 诊断工具

### 运行完整诊断

```bash
cd /root/FFC-AI
sudo bash scripts/diagnose-codex.sh
```

诊断脚本会检查：
- ✓ 系统环境和必需命令
- ✓ Codex 配置文件存在性
- ✓ 配置项正确性
- ✓ 是否有自定义 provider 配置
- ✓ API key 和 base_url 配置
- ✓ 网络连接性
- ✓ 服务运行状态
- ✓ 最近的错误日志

### 查看实时日志

```bash
# Telegram bot 日志
sudo journalctl -u ai-telegram-bot -f

# 只看错误
sudo journalctl -u ai-telegram-bot -f | grep -i "error\|reconnecting\|failed"

# 最近100行
sudo journalctl -u ai-telegram-bot -n 100 --no-pager
```

## 验证修复

修复后，测试步骤：

1. **重启服务**
   ```bash
   sudo systemctl restart ai-telegram-bot
   ```

2. **检查服务状态**
   ```bash
   sudo systemctl status ai-telegram-bot
   ```

3. **在 Telegram 中测试**
   ```text
   你好
   列出当前目录的文件
   ```

4. **查看配置**
   ```bash
   cat ~/.codex/config.toml
   grep -A 10 "\[model_providers.ffc_openai_compat\]" ~/.codex/config.toml
   ```

## 常见问题

### Q1: 修复后仍然出现 "Reconnecting... 5/5"

**可能原因：**
- API key 错误或过期
- base_url 地址错误
- 代理服务器本身不稳定
- 网络连接问题

**解决方案：**
```bash
# 1. 验证 API key
curl -H "Authorization: Bearer sk-你的key" https://你的代理/v1/models

# 2. 检查配置
cat ~/.codex/config.toml
cat /var/lib/ai-remote-runner/config.env | grep CODEX

# 3. 测试网络连接
curl -I https://你的代理地址/v1

# 4. 查看详细错误
sudo journalctl -u ai-telegram-bot -n 50 --no-pager
```

### Q2: 修复脚本找不到 base_url

**解决方案：**
手动提供 base_url：

```bash
export CODEX_BASE_URL="https://你的代理地址/v1"
sudo -E bash scripts/fix-codex-reconnecting.sh
```

或者在脚本提示时输入。

### Q3: Windows 上配置不同怎么办

Windows 和 Linux 配置可以不同：
- **Windows**：可以继续使用默认 websocket 配置
- **Linux 服务器**：必须使用本指南的修复配置

这是正常的环境差异，不需要保持一致。

### Q4: 使用官方 OpenAI API 需要修复吗

**不需要。** 

如果 `CODEX_BASE_URL` 是官方的 `https://api.openai.com/v1`，则不需要这些修复。官方 API 的 websocket 实现是稳定的。

## 技术原理

### 配置对比

**默认配置（可能不稳定）：**
```toml
model_provider = "openai"
openai_base_url = "https://third-party-proxy.com/v1"
# 默认使用 websocket
# 默认较短的超时和重试次数
```

**优化配置（稳定）：**
```toml
model_provider = "ffc_openai_compat"

[model_providers.ffc_openai_compat]
base_url = "https://third-party-proxy.com/v1"
wire_api = "responses"              # HTTP 而非 websocket
supports_websockets = false          # 禁用 websocket
request_max_retries = 6              # 增加重试
stream_max_retries = 10              # 增加流式重试
stream_idle_timeout_ms = 600000      # 10分钟超时
```

### 代码层面

修复在以下位置生效：

1. **安装脚本** (`scripts/install-runner.sh:886-930`)
   - 检测非官方 base_url
   - 自动生成自定义 provider 配置

2. **Runtime 配置** (`src/ai_remote_runner/runtime_config.py:330-350`)
   - `_codex_config_with_openai_compatible_provider` 函数
   - 写入优化参数

3. **Provider 层** (`src/ai_remote_runner/providers.py:847-932`)
   - 检测 "reconnecting..." 错误
   - 提供诊断信息和修复建议

## 相关文件

- 配置文件：`~/.codex/config.toml`
- 认证文件：`~/.codex/auth.json`
- Runner 环境：`/var/lib/ai-remote-runner/config.env`
- 修复脚本：`scripts/fix-codex-reconnecting.sh`
- 诊断脚本：`scripts/diagnose-codex.sh`
- 安装脚本：`scripts/install-runner.sh`

## 获取帮助

如果以上方法都无法解决问题：

1. 运行完整诊断并保存输出：
   ```bash
   sudo bash scripts/diagnose-codex.sh > codex-diagnostic.log 2>&1
   ```

2. 查看最近的错误日志：
   ```bash
   sudo journalctl -u ai-telegram-bot -n 200 --no-pager > telegram-bot.log
   ```

3. 提交 issue 时附上：
   - `codex-diagnostic.log`
   - `telegram-bot.log`
   - 你的 base_url（隐藏敏感部分）
   - 代理类型（如：OpenRouter, APIpie 等）

GitHub Issues: https://github.com/vpn3288/FFC-AI/issues

---

**最后更新：** 2026-06-24  
**适用版本：** FFC-AI v1.0+
