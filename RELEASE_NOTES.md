### 🎯 Metis v1.2.3 更新日志 (Release Notes)

#### 🐛 问题修复 (Bug Fixes)
1. **空文件写入与初始化填充修复 (Empty File Population Fix)**
   - 修复了对 0 字节全新空文件写入内容时由于 SEARCH 块匹配失败而无法成功修改的缺陷；
   - 对空文件增加了针对性的初始化直写处理逻辑，并在系统提示词中显式引导 `<metis_write>` / `<metis_replace>` 操作，确保空文件首次填充百分之百成功。
2. **对话与输入框横向拉伸上限解除 (Uncapped Full-Width Dialog & Composer)**
   - 解除了消息列表行容器（原先限制最大 860px）以及底部输入框（原先限制最大 820px）的宽度上限；
   - 窗口横向放大或全屏拉伸时，对话流气泡、代码高亮框、终端输出面板与输入框均自适应铺满整屏，提供更开阔的代码阅读与多栏视野。
3. **移除多余的完成提示横幅 (Removed Redundant Completion Banner)**
   - 移除了会话轮次结束时弹出的 `Completed all requested tasks` 横幅，保持会话流精简清爽，避免对操作流程的视觉干扰。

#### 🚀 新增与优化功能 (New Features & Improvements)
1. **Gemini 思考等级与官方 API 架构深度规范 (Gemini Thinking Budget & SSE Streaming)**
   - 严格对照 Google Gemini 官方最新规范（Gemini 2.5 Flash / Pro），重新设计思考强度等级与 API 负载参数：
     - `off`：`thinkingBudget: 0`（完全关闭深度思考）
     - `low`：`thinkingBudget: 2048`
     - `medium`：`thinkingBudget: 8192`
     - `high`：`thinkingBudget: 24576`
   - 对不支持 thinkingConfig 的模型（如 Gemini 1.5 系列）自动省略该字段，避免 400 Bad Request 错误，并具备自动重试降级保护；
   - 完善对流式传输中 `thought: true` 部分的解析，自动封装并激活前端实时思考呼吸动效与脉冲光点，且不污染代码生成与工具调用。
2. **默认模型与预设列表升级 (Default Model & Presets Upgrade)**
   - 全局默认模型更新为速度与推理能力平衡的 `gemini-2.5-flash`，并预设内置 `gemini-2.5-pro`、`gemini-2.0-flash` 与 `gemini-1.5-pro`。

#### 📦 安装包分发 (Supported Packages)
- **Windows**: `Metis_1.2.3_x64-setup.exe` (NSIS 便携式安装包，支持完整卸载)
- **macOS**: `Metis_1.2.3_aarch64.dmg` (Apple Silicon 芯片专用便携镜像，M1/M2/M3/M4)
- **Linux**: `metis_1.2.3_amd64.deb` (Debian / Ubuntu) 与 `Metis_1.2.3_amd64.AppImage` (通用便携镜像)
