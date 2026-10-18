### 🎯 Metis v1.2.2 更新日志 (Release Notes)

#### 🐛 问题修复 (Bug Fixes)
1. **思考呼吸动画恢复 (Thinking Breathing Effect)**
   - 修复了发出请求后思考动画（`Thinking...` 呼吸动效与脉冲光点）消失的问题。在模型生成回复或流式输出思考标签期间，即时展示呼吸动效状态。
2. **列表内缩进代码块渲染修复 (Indented Code Block Rendering)**
   - 修复了 Markdown 列表项中包含缩进围栏代码块（如 `    ```cmd`）时无法正确渲染为语法高亮代码块的问题，自动剔除缩进并渲染独立代码容器。
3. **Markdown 链接断裂与下划线冲突修复 (Link Parser Fix)**
   - 修复了含有下划线 `_` 的 URL（如 `.../7-zip_portable`）与 `target="_blank"` 中的下划线被斜体规则 `_..._` 误匹配导致 HTML 标签错乱和链接断裂的问题。
4. **空会话重复创建与侧边栏过滤修复 (Empty Sessions Cleanup)**
   - 修复了点击新建会话时会产生多个无用空会话的问题；侧边栏现已只显示有内容的历史会话，空会话自动过滤隐藏。
5. **Windows 任务栏与窗口图标修复 (Window & Taskbar Icon)**
   - 修复了在 Windows 操作系统窗口栏和任务栏中未正确显示 Metis 应用图标的问题。

#### 🚀 新增功能 (New Features)
1. **外部链接系统浏览器打开 (Open Links in System Browser)**
   - 聊天消息中的所有超链接（HTTP / HTTPS / Mailto）点击后自动调用系统默认外部浏览器打开，不再受内嵌 WebView 限制。
2. **对话文本与代码自由选中与复制 (Free Text Selection & Copy)**
   - 移除了对聊天对话框的文本选择限制，用户现在可以自由滑动鼠标选中、高亮或复制气泡文本、代码段与终端输出内容。
3. **503 / 瞬态 API 错误指数退避重试 (Exponential Backoff Retry)**
   - 遇到 LLM 接口返回 503（服务暂时不可用）或网络超时等瞬态错误时，自动启动 **2s → 4s → 8s** 指数退避重试（最多 3 次），并实时显示倒计时；支持随时手动中断。
4. **首轮对话会话标题智能总结 (Auto-Summarize Session Title)**
   - 新会话首次对话时自动在提示词中附加标题总结指令，从模型回复中提取 `<metis_title>` 标签并自动重命名会话标题，同步更新顶部栏与侧边栏。

#### 📦 安装包分发 (Supported Packages)
- **Windows**: `Metis_1.2.2_x64-setup.exe` (NSIS 便携式安装包，支持完整卸载)
- **macOS**: `Metis_1.2.2_aarch64.dmg` (Apple Silicon 芯片专用便携镜像，M1/M2/M3/M4)
- **Linux**: `metis_1.2.2_amd64.deb` (Debian / Ubuntu) 与 `Metis_1.2.2_amd64.AppImage` (通用便携镜像)
