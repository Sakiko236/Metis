### 🎯 Metis v1.4.2 更新日志 (Release Notes)

#### 🚀 界面体验与交互升级 (UI Refinements & User Experience)
1. **对话体系升级 (Chat Terminology Alignment)**
   - 全面将此前过于正式晦涩的 `session` 术语替换为亲切直观的 `chat`（包括新建、重命名、删除、空状态以及各类操作提示与通知）。
   - 规范侧边栏对话分组标签为 `Local chats`，移除了多余的 `(Auto-saved)` 提示，修复了 CSS 全大写转换使其正确遵循仅首单词首字母大写的规范。

2. **精细化重构 `+ New chat` 按钮 (Redesigned Compact New Chat Button)**
   - 将原先突兀笨重、高度过大的实体渐变蓝按钮重构为高度 32px 的现代轻量通透卡片按钮。
   - 采用精致天青微透背景与发光描边（`rgba(14, 165, 233, 0.08)` / `border: 1px solid rgba(14, 165, 233, 0.22)`），提供平滑的 Hover 发光与按下反馈，并完美适配浅色主题（Light Theme）。
   - 优化侧边栏按钮间距，与搜索输入框比例严谨对齐。

3. **输入工具栏精简 (Composer Toolbar Cleanup)**
   - 彻底移除输入框底部已无实际作用的 Web 按钮及多余样式与前缀插入脚本，保留文件、目录、多媒体与思考强度控制，界面更加清爽专注。

#### 📦 安装包分发 (Supported Packages)
- **Windows**: `Metis_1.4.2_x64-setup.exe` (NSIS 便携式安装包，内置 Python Sidecar 引擎，支持完整卸载)
- **macOS**: `Metis_1.4.2_aarch64.dmg` (Apple Silicon 芯片专用便携镜像，M1/M2/M3/M4)
- **Linux**: `metis_1.4.2_amd64.deb` (Debian / Ubuntu) 与 `Metis_1.4.2_amd64.AppImage` (通用便携镜像)

---

### 🎯 Metis v1.4.1 更新日志 (Release Notes)

#### 🚀 核心优化与 Bug 修复 (Improvements & Bug Fixes)
1. **全面修复 LaTeX `\[ \]` 与 `\\[ \\]` 数学公式渲染 (Markdown LaTeX Rendering Fix)**
   - **块级展示公式前置安全提取**：引入 Pre-extraction 解析机制，在 Markdown 文本进入段落与行切分前提取全部跨行、单行 `\[ ... \]` / `\\[ ... \\]` 与 `$$ ... $$` 公式为安全占位符，彻底修复多行公式因逐行调用 `mdInline` 丢失匹配导致的未渲染问题。
   - **转义反斜杠与全场景排版兼容**：正则全面覆盖 `\{1,4}\[` / `\{1,4}\]` 与 `\{1,4}\(` / `\{1,4}\)`，彻底消除大模型输出 JSON 转义引起的定界符残留在 HTML 和 KaTeX 中引发的语法错误。
   - **段落、多行列表与表格全覆盖**：无论是独立成块的公式卡片、段落内部文字包裹、多行列表延续项（continuation line）还是表格单元格内部，数学公式均能完整、居中并高亮渲染，且绝不破坏代码块与行内代码。

2. **Gemini 免费层搜索配额超限 429 自动无感降级 (Gemini 429 Free Tier Search Grounding Downgrade)**
   - **智能识别配额限制并重试**：针对 Google AI Studio 免费层 key 不包含 Google Search Grounding 配额导致直接报错 `429 RESOURCE_EXHAUSTED` 的情况，服务端在遭遇 429 / 400 / 403 时自动摘除 `tools: [{"google_search": {}}]` 并立即无缝回退至纯文本推理，消除用户对话中断。
   - **全厂商对齐降级机制**：同步为 Claude 与 OpenAI / DeepSeek Responses API 加入 429 速率/配额超限时的工具降级重试逻辑，确保任意模型在检索受限时依然可用。

3. **端到端测试套件扩充**
   - 新增第 10 项端到端自测用例：自动化验证 Gemini 429 搜索工具自动移除与降级回退机制。

#### 📦 安装包分发 (Supported Packages)
- **Windows**: `Metis_1.4.1_x64-setup.exe` (NSIS 便携式安装包，内置 Python Sidecar 引擎，支持完整卸载)
- **macOS**: `Metis_1.4.1_aarch64.dmg` (Apple Silicon 芯片专用便携镜像，M1/M2/M3/M4)
- **Linux**: `metis_1.4.1_amd64.deb` (Debian / Ubuntu) 与 `Metis_1.4.1_amd64.AppImage` (通用便携镜像)
