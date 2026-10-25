# `src-python` 目录文件用途详解

`src-python/` 是 **Metis** 的核心业务与 AI 引擎目录（Python Sidecar Engine）。它由 Tauri 宿主进程在后台以无头（Headless）子进程方式拉起，通过标准输入/输出（`stdin` / `stdout`）使用行分隔的 `METIS_IPC:<json>` 协议与前端及 Rust 宿主进行全双工异步通信。

整个引擎仅依赖 Python 3.10+ 标准库运行（无第三方 C 扩展运行时依赖），确保极小的打包体积与跨平台稳定性。

---

## 目录结构速览

```text
src-python/
├── main.py            # Sidecar 进程入口、异步 IPC 消息路由与多会话 Agent 循环调度器
├── agent.py           # Agent 运行状态控制器与系统提示词/文件快照构建器
├── llm.py             # 多厂商 LLM 流式调用客户端、多模态载荷组装与输出解析器
├── whitelist.py       # 零爆炸半径（Zero-Blast-Radius）文件白名单与原地编辑引擎
├── models.py          # 核心领域模型、IPC 事件结构与 JSON 序列化/反序列化定义
├── config.py          # 跨平台配置文件（config.json）读写、环境变量注入与旧版迁移
├── session.py         # 聊天会话持久化存储（sessions.json）管理
├── shell.py           # 异步无窗口终端命令执行器（带 PyInstaller 环境隔离与超时控制）
├── requirements.txt   # PyInstaller 独立二进制打包依赖清单
├── README.md          # Python Sidecar 英文架构概览文档
└── summary.md         # 本文件：详细注明目录下每个文件的职责与内部实现细节
```

---

## 各文件详细用途说明

### 1. [`main.py`](main.py) — Sidecar 进程主入口、IPC 路由与自主循环引擎
作为 Python Sidecar 的启动入口与核心协调器（Orchestrator），负责串联配置、会话、白名单与大模型调用。

* **主要职责与核心组件**：
  * **标准流 IPC 通信 (`emit_host_message`, `run_ipc_loop`)**：
    * 强制将 `stdout` / `stderr` 重配置为 UTF-8 编码。
    * 启动后台守护线程读取父进程（Tauri）通过 `stdin` 发送的 JSON 指令并推入 `asyncio.Queue`；当检测到 `stdin` 管道关闭（EOF）时立即调用 `os._exit(0)` 退出，杜绝孤儿进程。
    * 使用线程锁 `_stdout_lock` 将发往宿主的消息封装为 `METIS_IPC:<json>\n` 写入 `stdout`。
  * **核心引擎状态管理 (`SidecarEngine`)**：
    * 维护全局配置 `AppConfig`、会话仓库 `SessionStoreData`，以及按会话隔离的 `AgentController`、任务句柄 `asyncio.Task`、状态文本与 `WhitelistManager`。
    * `broadcast_full_state()`：向前端广播完整应用状态（版本号、配置、所有会话、当前激活会话、白名单目录、忙碌状态及 Toast 提示）。
  * **前端/宿主指令分发 (`handle_command`)**：
    * **生命周期与探活**：`ping`（返回 `pong` 及版本号）、`uiReady`（广播初始状态）。
    * **会话管理**：`createSession`（复用空会话或新建会话）、`selectSession`（切换会话并重新绑定白名单）、`renameSession`、`deleteSession`（取消运行中任务并清理状态）。
    * **配置与模型探测**：`saveConfig`、`setTheme`、`setThinkingIntensity`（调整思考预算强度）、`probeOllama`（探测本地 Ollama 可用模型列表）。
    * **文件白名单与多模态挂载**：`addFiles`、`addDirectory`、`removeFile`、`removeDirectory`、`clearWhitelist`、`pasteImage` / `addMediaBlob`（将剪贴板或拖拽的 Base64 图片/PDF 解码写入临时目录 `metis_media` 并加入白名单）。当已有对话历史时，向 `api_history` 追加 `[Workspace Event]` 变更记录。
    * **文件审查与回滚**：`inspectFile`（读取文件实时内容、生成图片/PDF 的 Data URL、通过 `compute_diff_lines` 计算相较初始内容的逐行 Diff 并返回 `fileContentPreview`）、`revertFile`（将文件恢复至刚挂载时的初始内容）。
    * **Agent 循环控制**：`sendPrompt`、`pauseLoop`、`resumeLoop`、`stopLoop`。
  * **多轮自主执行与自愈循环 (`run_autonomous_loop`)**：
    * 采用 **Append-Only（仅追加）事件日志架构**：在会话首轮对话开头注入不可变的 `[Workspace Initial Files Snapshot]`，以最大化命中大模型 API 的前缀缓存（Prefix Cache）。
    * 首轮对话自动注入 `<metis_title>` 标题生成指令，从模型回复中提取精炼会话标题。
    * 调用 `MultiVendorClient.send_chat` 进行流式生成，通过 `on_chunk` 回调实时过滤内部标签并向前端发送 `streamUpdate` 事件。
    * 针对网络波动或 HTTP `429`/`500`/`502`/`503`/`504` 错误实现指数退避自动重试（`2s`、`4s`、`8s`）。
    * 解析模型输出中的文件修改指令（`<metis_replace>` / `<metis_write>`），调用 `WhitelistManager.apply_edit_in_place` 原地修改文件，计算 Diff 并生成 `file_edit` 或 `security_block` UI 事件；若存在编辑匹配失败，自动将错误反馈追加至对话历史并触发下一轮自愈重试。

---

### 2. [`agent.py`](agent.py) — Agent 状态控制器与系统提示词构建器
定义单会话 Agent 运行状态标志位，以及构造发往大模型的静态系统提示词与初始文件快照。

* **主要职责与核心组件**：
  * **`AgentController`**：
    * 维护单个会话的执行控制状态：`is_busy`（是否正在运行）、`is_paused`（是否已暂停）、`stop_requested`（是否请求中止），提供 `pause()`、`resume()`、`stop()` 方法供异步循环轮询响应。
  * **`AgentLoop.build_system_prompt()`**：
    * 生成包含当前系统日期的确定性系统提示词（作为 Prefix Cache 锚点）。
    * 严格约束 Agent 的安全边界：仅允许修改白名单内的文件，严禁新建或删除文件；规定使用 `<metis_replace file="...">`（配合 `<<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE`）进行精确局部替换，或使用 `<metis_write file="...">` 进行空文件填充/整文件重写；启用原生联网搜索（Web Search Grounding）指导原则，并提醒模型标签内容对用户不可见、需附带清晰的自然语言说明。
  * **`AgentLoop.build_file_snapshot(files)`**：
    * 将当前会话白名单中的所有文件格式化为 `### WHITELISTED FILES CONTENT SNAPSHOT` 文本块，包含相对路径、完整路径及文件内容（对 0 字节空文件给出显式标记与填充提示）。

---

### 3. [`llm.py`](llm.py) — 多厂商 LLM 流式客户端与输出解析引擎
基于 Python 标准库 `urllib.request` 与 `ssl` 实现的零外部依赖多厂商大模型客户端，同时负责流式清洗与文件编辑指令解析。

* **主要职责与核心组件**：
  * **统一流式请求入口 (`MultiVendorClient.send_chat`)**：
    * 将静态 `system_prompt` 与动态历史消息解耦，提取白名单中的二进制多模态文件（图片/PDF Base64），并在独立线程（`asyncio.to_thread`）中分发至对应厂商实现。
  * **三大主流协议适配**：
    1. **Google Gemini (`_call_gemini_sync`)**：调用 `:streamGenerateContent?alt=sse` 接口；支持通过 `inlineData` 传入图片与 PDF；支持 `thinkingConfig.thinkingBudget`（`low: 2048`, `medium: 8192`, `high/max: 24576`）与原生 `google_search` Grounding 工具；当免费额度或模型不支持搜索/思考配置导致报错时，自动降级剥离对应参数重试；过滤 `thought: true` 内部思考分片。
    2. **Anthropic Claude (`_call_claude_sync`)**：调用 `/v1/messages` 流式接口；支持图片块与 PDF 文档块（自动附加 `anthropic-beta: pdfs-2024-09-25` 请求头）；支持 Claude 3.7 Sonnet 扩展思考预算（`thinking.budget_tokens`）与原生 `web_search_20250101` 工具，不支持时自动降级重试。
    3. **OpenAI Compatible / DeepSeek (`_call_responses_api_sync` & `_call_chat_completions_fallback_sync`)**：优先尝试标准 `/v1/responses` 接口（支持 `web_search` 工具与多模态 `input_image`，自动过滤推理模型 `response.reasoning*.delta` 防止思维链泄漏）；若服务端返回 `404`/`405`/`501` 或参数不兼容，无缝回退至标准 `/v1/chat/completions` 接口。
  * **历史消息规范化 (`prepare_gemini_contents`, `prepare_claude_messages`, `prepare_responses_inputs`, `prepare_chat_completions_messages`)**：
    * 自动合并连续相同角色的发言，确保以 `user` 角色开头且不以 `assistant`/`model` 角色结尾（防止 Gemini 400 报错及 Claude 预填充冲突）。
  * **思考块、标题与编辑指令解析 (`strip_leading_think`, `strip_leading_title`, `extract_session_title`, `_scan_agent_blocks`, `parse_agent_output`, `format_display_text`)**：
    * 精确剥离回复开头的 `<think>...</think>` 思考过程与 `<metis_title>...</metis_title>` 标题标签（流式未闭合时同样安全隐藏，且绝不误伤正文中间提及的同名字符串）。
    * **代码块感知扫描器 (`_scan_agent_blocks`)**：识别顶层 Markdown 围栏代码块（```` ``` ```` 或 `~~~`），确保模型在正文中举例说明的 `<metis_replace>` / `<metis_write>` 不会被误执行或误删，仅提取顶层真实的编辑指令块并从用户可见文本中隐藏。
  * **本地 Ollama 探测 (`fetch_ollama_models`)**：请求 `/api/tags` 获取本地已安装的 Ollama 模型列表。

---

### 4. [`whitelist.py`](whitelist.py) — 零爆炸半径文件白名单与原地编辑引擎
实现 Metis 最核心的安全承诺——“零爆炸半径（Zero Blast Radius）”：Agent 只能修改用户显式加入白名单的现有文件，严禁创建新文件或删除文件。

* **主要职责与核心组件**：
  * **白名单与挂载目录管理 (`WhitelistManager`)**：
    * `add_file` / `remove_file` / `add_directory` / `remove_directory` / `clear`：管理当前会话授权的文件列表与文件夹。递归扫描目录时自动跳过 `.git`、`node_modules`、`target`、`__pycache__` 等无关目录（`IGNORED_DIRS`）以及 `.exe`、`.dll`、`.zip`、`.pyc` 等二进制/归档后缀（`IGNORED_EXTS`）。
    * `update_all_relative_paths()` & `get_active_workspace_directory()`：自动计算白名单文件相对于活动工作区的相对路径。
  * **多模态识别与纯标准库 PDF 文本提取 (`extract_pdf_text`)**：
    * 添加文件时自动识别图片（`png`/`jpg`/`jpeg`/`webp`/`gif`/`bmp`）与 `pdf`，标记 `media_kind`、`mime_type` 与 `is_binary_media`。
    * 对 PDF 文件使用标准库 `zlib` 解压流数据并配合正则提取 `Tj`/`TJ` 文本算子内容，兼顾纯文本上下文预览与原生多模态输入。
  * **智能文件解析 (`resolve_whitelisted_file`)**：
    * 支持按规范化绝对路径、工作区相对路径或唯一文件名匹配白名单中的目标文件。
  * **三级容错原地编辑引擎 (`apply_edit_in_place`, `try_apply_search_replace`)**：
    * 若目标文件不在白名单中，立即拦截并返回 `[Security Block - File Creation Prohibited]`。
    * 支持整文件写入（`WriteFull`）与局部搜索替换（`SearchReplace`）。`try_apply_search_replace` 采用三级匹配策略：
      1. **第一级**：精确子串匹配（Exact Match）。
      2. **第二级**：统一换行符（CRLF / LF 归一化）匹配，并在写回时保留文件原有的换行符风格。
      3. **第三级**：逐行去除首尾空白符（Line-by-line Whitespace-Trimmed）匹配，有效容忍大模型生成时的缩进微小偏差。
  * **一键快照回滚 (`revert_file_to_initial`)**：
    * 每个 `WhitelistedFile` 均保存挂载之初的 `initial_content`，随时可将磁盘文件无损还原至初始状态并重置编辑计数。

---

### 5. [`models.py`](models.py) — 领域数据模型与序列化 Schema
集中定义所有业务数据结构、枚举类型及与前端/持久化 JSON 交互的 `to_dict()` / `from_dict()` 映射方法（兼容 PascalCase 与 snake_case 键名）。

* **核心数据结构**：
  * `VendorType` (`IntEnum`)：枚举厂商类型（`Gemini = 0`, `OpenAI = 1`, `Claude = 2`, `DeepSeek = 3`, `Ollama = 4`, `CustomOpenAI = 5`），支持从整数或多种字符串别名解析。
  * `VendorProfile`：单个模型提供商配置（ID、显示名称、类型、BaseUrl、ApiKey、当前选中模型、预设模型列表 `preset_models`、思考强度 `thinking_intensity`）。
  * `AppConfig`：全局应用配置（当前激活厂商、最大验证循环次数 `max_verify_loops`、命令超时秒数、主题 `theme`、思考强度及厂商列表），内置 `sanitize_thinking_intensity` 校验逻辑。
  * `WhitelistedFile`：白名单文件实体，记录完整路径、文件名、相对路径、媒体类型（`code`/`image`/`pdf`）、MIME 类型、初始内容 `initial_content`、最新保存内容 `last_saved_content`、编辑次数 `edit_count`、时间戳及字节大小。
  * `ChatHistoryItem`：发往大模型 API 的单条上下文记录（`role`, `content`）。
  * `SearchResultItem`：联网搜索结果条目（`title`, `url`, `snippet`）。
  * `UiStreamEvent`：前端聊天流渲染事件（支持 `user`、`assistant`、`file_edit`、`security_block`、`notice` 等类型，携带模型名称、轮次、文件 Diff 列表 `file_edits`、终端输出等元数据）。
  * `ChatSession` & `SessionStoreData`：单次会话实体（含专属白名单、`api_history`、`ui_events`）与多会话集合容器，提供 `find_empty_session()` 与 `prune_redundant_empty_sessions()` 自动清理多余空白会话（保护正在运行的忙碌会话不被清理）。
  * `FileEditActionType`、`FileEdit`、`ParsedAgentResponse`：封装解析后的模型输出文本与待执行的文件修改动作列表。
  * `VerifyExecutionResult`：封装终端命令执行结果（退出码、标准输出/错误、耗时、是否超时/取消）。

---

### 6. [`config.py`](config.py) — 跨平台配置持久化与版本/迁移管理器
负责应用级配置文件 `config.json` 的路径解析、默认初始化、旧版本配置迁移与环境变量密钥注入。

* **主要职责与核心方法**：
  * **平台标准目录解析 (`get_base_config_dir`, `get_app_data_dir`, `get_config_path`)**：
    * Windows：`%APPDATA%/Metis/config.json`
    * macOS：`~/Library/Application Support/Metis/config.json`
    * Linux：`$XDG_CONFIG_HOME/Metis/config.json` 或 `~/.config/Metis/config.json`
  * **动态应用版本号获取 (`get_app_version`)**：
    * 优先读取 Tauri 宿主注入的环境变量 `METIS_APP_VERSION`；开发模式下自动向上查找并解析 `package.json` 或 `src-tauri/Cargo.toml` 中的版本号。
  * **三大标准厂商预设与无缝迁移 (`create_default_config`, `migrate_to_three_providers`)**：
    * 标准内置三大提供商配置：**Google Gemini**、**Anthropic Claude**、**OpenAI Compatible**（涵盖 OpenAI、DeepSeek、Ollama 等兼容接口）。
    * 自动将旧版分散的 `deepseek`/`openai`/`ollama`/`custom` 配置平滑迁移合并至 `openai_compatible`，保留用户的 API Key、Base URL 与常用模型列表。
  * **环境变量自动补全 (`populate_env_keys`)**：
    * 若配置文件中未填写 API Key，自动尝试从系统环境变量（`GEMINI_API_KEY`、`GOOGLE_API_KEY`、`ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、`DEEPSEEK_API_KEY`）读取填充。

---

### 7. [`session.py`](session.py) — 会话历史持久化管理器
管理多会话聊天记录与工作区白名单状态的本地磁盘持久化。

* **主要职责与核心方法**：
  * **`SessionManager.get_sessions_file_path()`**：定位平台应用数据目录下的 `sessions.json`。
  * **`SessionManager.load_or_create_default()`**：启动时从 `sessions.json`（兼容旧版当前目录 `sessions.json`）加载历史会话；若文件不存在或损坏，自动创建包含默认空会话的 `SessionStoreData` 并保存。
  * **`SessionManager.save(store)`**：将所有会话的聊天记录、UI 事件流及白名单快照以 UTF-8 JSON 格式持久化到磁盘。

---

### 8. [`shell.py`](shell.py) — 异步子进程命令执行器
提供跨平台、无黑窗闪烁、带超时保护与编码容错的子进程命令运行能力。

* **主要职责与核心方法**：
  * **编码自适应解码 (`ShellRunner._decode_bytes`)**：优先以 UTF-8 解码子进程输出，失败时自动回退至 GBK（兼容中文 Windows 控制台输出），杜绝乱码崩溃。
  * **PyInstaller 环境净化与静默执行 (`ShellRunner._run_sync`, `ShellRunner.run_command`)**：
    * 注入 `PYTHONUNBUFFERED=1`、`PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1`。
    * 主动剔除 `PYTHONHOME`、`_MEIPASS`、`_MEIPASS2` 及包含 `_MEIPASS` 的 `PYTHONPATH`，防止从冻结的 PyInstaller Sidecar 内部派生外部 Python 命令时发生动态库/解释器冲突。
    * 在 Windows 下设置 `CREATE_NO_WINDOW` (`0x08000000`) 标志位隐藏控制台窗口，支持自定义工作目录与超时强杀（`subprocess.TimeoutExpired`），返回结构化的 `VerifyExecutionResult`。

---

### 9. [`requirements.txt`](requirements.txt) — 构建与打包依赖清单
* 记录将 Python Sidecar 编译为独立可执行文件所需的构建期依赖（`pyinstaller>=6.0.0`）。
* 说明核心运行时仅使用 Python 3.10+ 标准库，无需安装任何第三方运行时包即可直接通过 `python -u src-python/main.py` 启动。

---

### 10. [`README.md`](README.md) — 模块英文概览文档
* 面向开发者的英文模块简介，包含核心文件速查表、零爆炸半径原则、多模态 Base64 序列化策略及 `METIS_IPC:` 通信机制说明。
