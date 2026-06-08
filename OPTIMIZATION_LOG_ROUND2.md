# 第二轮优化日志

## 审查者报告摘要

**日期**: 2026-06-07
**轮次**: Round 2

### 审查者1（Claude Code Reviewer）发现：

#### P1问题：
1. **命令系统仍有英文命令** - commands.py 包含约40个英文命令别名
   - 违反用户需求："所有命令必须使用中文"
   - 影响范围：所有Telegram和Mattermost用户
   - 严重程度：P1（必须修复）

2. **缺少命令使用模板** - 命令说明缺少可复制的使用模板
   - 用户需求："提供命令使用模板，让我复制粘贴修改就能直接使用"
   - 当前状态：只有description_zh，没有template_zh和example_zh
   - 严重程度：P1（必须修复）

3. **Codex第三方API配置未持久化** - install-runner.sh未将CODEX_BASE_URL写入~/.codex/config.toml
   - 问题：环境变量CODEX_BASE_URL只在安装时生效，重启后丢失
   - 影响：用户使用第三方API时，配置不会持久化
   - 严重程度：P1（必须修复）

### 审查者2（GPT-5.5 Reviewer）发现：

#### P1问题：
1. **英文命令混杂** - 同上
2. **命令模板缺失** - 同上  
3. **Telegram命令配置修改未验证** - 无法确认通过Telegram修改的配置是否正确持久化

#### P2问题：
1. **README优化** - 需要添加更清晰的第三方API配置说明
2. **命令帮助信息** - 需要显示更友好的命令使用示例

## 主笔决策

### 决策1：删除所有英文命令
**状态**: 采纳
**理由**: 用户明确要求"只使用中文命令"，英文命令违反核心需求
**实施**: 
- 删除commands.py中所有英文key的命令
- 保留中文命令
- 例外：保留codex/claude等工具名作为参数，但命令本身必须是中文

### 决策2：为所有命令添加使用模板
**状态**: 采纳
**理由**: 用户明确要求"提供命令使用模板，让我复制粘贴修改就能直接使用"
**实施**:
- 在CommandSpec添加template_zh和example_zh字段
- 为每个命令提供完整的使用模板
- 模板格式: `/ai <命令> <参数说明>`

### 决策3：修复Codex配置持久化
**状态**: 采纳
**理由**: 第三方API配置必须持久化，这是用户核心需求
**实施**:
- 修改install-runner.sh，将CODEX_BASE_URL写入~/.codex/config.toml
- 同时写入/var/lib/ai-remote-runner/config.env
- 验证配置在重启后仍然有效

## 修复计划

### 第一步：修复commands.py
- [ ] 删除所有英文命令key
- [ ] 添加template_zh字段
- [ ] 为每个命令添加使用模板

### 第二步：修复install-runner.sh  
- [ ] 添加Codex config.toml写入逻辑
- [ ] 验证配置持久化

### 第三步：创建配置验证脚本
- [ ] 创建scripts/validate-config.sh
- [ ] 验证Codex配置
- [ ] 验证Claude Code配置

### 第四步：验证修复
- [ ] 运行测试
- [ ] 验证P0/P1/P2问题都已解决
- [ ] 继续下一轮审查
