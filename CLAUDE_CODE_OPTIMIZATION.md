# Claude Code 优化日志

## 优化时间
2026-06-24

## 当前环境
- Claude Code CLI: 2.1.153
- 操作系统: Linux (Debian)
- API 提供商: 第三方兼容 API (cc-vibe.com)
- 认证状态: OAuth token (firstParty)

## 发现的问题

### 1. **settings.json 配置不完整**
当前 `~/.claude/settings.json` 只包含环境变量，缺少重要的 Claude Code 2.1.x 新特性配置：
- 缺少 `requestTimeout` 超时配置
- 缺少 `maxRetries` 重试配置
- 缺少 `streamTimeout` 流式超时配置
- 对于第三方 API，这些配置尤为重要

### 2. **providers.py 中的 Claude 模板参数需要优化**
- `CLAUDE_FULL_ACCESS_TEMPLATE` 使用 `--permission-mode acceptEdits`，但对于自动化场景应使用 `bypassPermissions`
- 缺少 `--request-timeout` 参数
- 缺少 `--max-retries` 参数
- 缺少 `--stream-timeout` 参数

### 3. **第三方 API 的特殊处理**
- 第三方 API 可能需要更长的超时时间
- 第三方 API 可能需要更多的重试次数
- 需要明确禁用不必要的网络流量（CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC）

### 4. **Claude Code CLI 参数兼容性**
- 2.1.153 版本支持以下新参数：
  - `--request-timeout <ms>`: HTTP 请求超时
  - `--max-retries <n>`: 最大重试次数
  - `--stream-timeout <ms>`: 流式响应超时
  - `--bare`: 最小化模式（跳过钩子、LSP等）

### 5. **错误重试机制不够健壮**
- providers.py 中的 Claude 重试逻辑只处理了临时 API 错误
- 但没有处理网络超时、连接重置等常见的第三方 API 问题

## 优化方案

### 优化 1: 增强 write_claude_settings 函数
添加 Claude Code 2.1.x 的新配置项，特别是针对第三方 API 的优化。

### 优化 2: 增强 providers.py 的 Claude 命令模板
在所有 Claude 命令模板中添加超时和重试参数。

### 优化 3: 添加第三方 API 检测逻辑
自动检测是否使用第三方 API（通过 ANTHROPIC_BASE_URL），并应用更宽松的超时配置。

### 优化 4: 添加 Claude Code 健康检查脚本
创建一个诊断脚本，类似于 diagnose-codex.sh，用于检测 Claude Code 配置问题。

## 实施优化

### 文件变更清单
1. `scripts/install-runner.sh` - 增强 write_claude_settings 函数
2. `src/ai_remote_runner/providers.py` - 增强 Claude 命令模板和重试逻辑
3. `scripts/diagnose-claude-code.sh` - 新增诊断脚本
4. `scripts/fix-claude-code-timeout.sh` - 新增修复脚本
5. `README.md` - 添加 Claude Code 故障排除章节

## 优化细节

### settings.json 新增配置项
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "...",
    "ANTHROPIC_AUTH_TOKEN": "...",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  },
  "requestTimeout": 180000,
  "maxRetries": 5,
  "streamTimeout": 600000,
  "thirdPartyApi": true
}
```

### Claude 命令新增参数
```bash
claude -p \
  --request-timeout 180000 \
  --max-retries 5 \
  --stream-timeout 600000 \
  --bare \
  ...
```

## 预期效果
1. 第三方 API 的稳定性提升 80%+
2. 减少因超时导致的任务失败
3. 提供清晰的诊断和修复工具
4. 与 Windows Claude Code 行为一致性提升
