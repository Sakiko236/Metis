### 🎯 Metis v1.3.0 更新日志 (Release Notes)

#### 🚀 新增功能与架构重构 (New Features & Architecture)
1. **全新 Tauri 2.0 外壳 + Python Sidecar 混合架构 (Tauri + Python Sidecar Engine)**
   - **极简 Rust 原生宿主 (`src-tauri/`)**：Rust 专注于桌面窗口管理、无边框控制、原生文件/文件夹选择对话框与子进程生命周期守护。
   - **纯 Python 业务与智能体引擎 (`src-python/`)**：将大模型流式交互（Gemini / Claude / OpenAI / DeepSeek / Ollama）、白名单安全校验、Search/Replace 原地修改引擎、命令行验证、配置与会话管理全面重构为模块化 Python 架构（`models.py`、`config.py`、`session.py`、`whitelist.py`、`agent.py`、`llm.py`、`shell.py`、`main.py`）。
   - **零端口冲突双向管道通信 (`METIS_IPC`)**：采用基于 `stdin/stdout` 的异步双向 JSON 流管道通信，避免本地端口占用与防火墙弹窗，且当主窗口关闭或异常退出时 Python 进程自动零残留退出。
   - **独立打包免环境依赖**：通过 `scripts/build_sidecar.py` 与 PyInstaller 将 Python 引擎编译为独立原生二进制 `metis-engine` 并随 Tauri 安装包自动分发，终端用户无需安装任何 Python 环境。
2. **Gemini 2.5 思考预算与流式思考规范支持 (Gemini Thinking Budget & SSE Streaming)**
   - 完整支持 `thinkingConfig.thinkingBudget` 三档调节（`low=2048`、`medium=8192`、`high=24576`），流式解析 `thought: true` 分块并实时激活前端呼吸动效。
3. **OpenAI 与 DeepSeek 全面迁移现代 Responses API 协议与原生搜索 (Modern Responses API & Native Search Grounding)**
   - **统一直连 Responses 规范 (`POST /v1/responses`)**：将 OpenAI、DeepSeek 及兼容厂商全面迁移至现代 Responses 协议，直接接收并解析语义级 SSE 流式事件（`response.output_text.delta`、`response.reasoning_text.delta` 与 `web_search_call` 等），在模型服务端原生执行集群联网检索（`tools: [{"type": "web_search"}]`），允许 API 搜索但不强制，并对未实现 Responses 接口的旧版中转网关保持无缝兼容与自动回退。
   - **厂商原生搜索全矩阵支持**：结合 Gemini `google_search` Grounding、Claude `web_search_20250101` 与 OpenAI / DeepSeek Responses 原生 web_search，模型根据对话需要自主联网检索，彻底摆脱外部冗余爬虫。

#### 🐛 问题修复 (Bug Fixes)
1. **Ollama 厂商枚举序列化与探测按钮修复 (VendorType Serialization Fix)**
   - 修复了厂商 `Type` 枚举序列化格式不一致导致设置面板中 Ollama 免 Key 状态识别及“Probe Local Ollama Models”按钮不显示的问题。
2. **Windows 中文环境下 UTF-8 标准流编码修复 (Windows UTF-8 Stream Fix)**
   - 强制锁定 Python Sidecar 标准输入输出流为 `UTF-8` 编码，彻底消除在 Windows GBK 控制台代码页下输出特殊符号时的编码异常。
3. **空文件初始化写入、全宽自适应对话框与无冗余完成横幅**
   - 完整继承并验证 0 字节空文件首次写入填充、聊天与输入框横向无上限自适应拉伸、以及去除多余完成横幅特性。

#### 📦 安装包分发 (Supported Packages)
- **Windows**: `Metis_1.3.0_x64-setup.exe` (NSIS 便携式安装包，内置 Python Sidecar 引擎，支持完整卸载)
- **macOS**: `Metis_1.3.0_aarch64.dmg` (Apple Silicon 芯片专用便携镜像，M1/M2/M3/M4)
- **Linux**: `metis_1.3.0_amd64.deb` (Debian / Ubuntu) 与 `Metis_1.3.0_amd64.AppImage` (通用便携镜像)
