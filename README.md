# Astrbot记忆暂存区插件[Thoughts]
# astrbot-plugin-thoughts

（快了快了，因为自家AI要用）

**Thoughts - 轻量级时效性记忆插件**

为你的AI助手提供三层短中期记忆管理：

- 工作记忆：会话内临时暂存，用 [暂存] 标签随时记录。
- 中期记忆：跨会话共享，每日自动清理，告别永久记忆的臃肿。
- 私人思考：隐藏记录到历史，AI自己可见，用户无感。

非永久、轻量级，智能管理上下文，让AI更专注、更灵活。


**Thoughts - Lightweight Time-sensitive Memory Plugin**

Empower your AI assistant with a three-tier short-to-medium term memory mechanism:
- Working Memory – temporary storage within a conversation.
- Interim Memory – shared across conversations, automatically cleared daily.
- Private Thoughts – recorded discreetly into context, visible only to the AI.

Temporary memory intelligently manages context, bridges conversations, and keeps AI focused and flexible.

---

## 🚀 功能特性 | Features

### 1. 工作记忆 (Working Memory)
AI 在回复中可以使用 `[暂存]` 标签来记录一些临时想法、待办事项或计划。
- **用户无感**：`[暂存]` 及其之后的内容会被插件自动截获，不会发送给用户。
- **自动注入**：在下一次对话时，暂存的内容会自动注入到 System Prompt，提醒 AI 之前的思路。
- **会话隔离**：每个对话线程独立，新对话开启时自动清空。

### 2. 中期记忆 (Interim Memory)
通过工具 `record_interim_memory` 记录那些需要跨对话记住，但不需要永久保存的信息。
- **跨对话共享**：在不同的聊天窗口/线程中都能被 AI 回想起来。
- **定时清理**：支持 Cron 表达式配置清理时间（默认每天 23:55），防止记忆堆积导致的上下文臃肿。

### 3. 私人思考 (Private Thoughts)
通过工具 `record_private_thought` 记录 AI 的内心独白或社交感悟。
- **隐形记录**：内容直接存入聊天历史记录，用户在界面上完全不可见。
- **上下文关联**：作为历史背景的一部分，帮助 AI 在后续对话中保持一致的个性和深度的思考。

---

## 🛠️ 使用方法 | Usage

### AI 如何使用暂存区
AI 只需在回复末尾添加标签即可：
> AI: 好的，我先帮你查询一下天气。[暂存] 我待会还要记得提醒她带伞。

*用户只会看到："好的，我先帮你查询一下天气。"*
*然后聊完两句后，AI: "今天可要记得带伞哦"*

### 工具调用
- `record_interim_memory(content="...")`: 记录中期记忆。
- `record_private_thought(thought="...")`: 记录私人思考。

---

## 📦 安装 | Installation

1. 手动安装 或 在Astrbot插件市场下载
2. 在插件配置面板中设置：
   - **中期记忆清理时间 (Cron格式)**: 默认 `55 23 * * *` (每天 23:55)。
     - 格式：`分 时 日 月 周`
     - 示例：`30 17 * * 3` (每周三 17:30)

