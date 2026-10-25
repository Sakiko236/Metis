# `src-tauri` 目录文件用途详解

`src-tauri/` 是 **Metis** 的原生桌面宿主外壳目录（基于 **Tauri 2.0 + Rust** 构建）。它作为一个超轻量级的监督者（Supervisor）进程运行，负责承载前端 WebView 界面、管理 Python Sidecar 引擎进程的生命周期、提供原生操作系统文件/文件夹选择对话框、拦截系统级文件拖拽事件，并在前端与 Python 引擎之间桥接异步 IPC 消息。

---

## 目录结构速览

```text
src-tauri/
├── src/
│   ├── main.rs            # 桌面端 Rust 二进制程序入口（配置 Windows 子系统并调用库入口）
│   └── lib.rs             # 核心宿主逻辑：Sidecar 进程管理、双向 stdio 桥接、原生对话框与窗口控制
├── capabilities/
│   └── default.json       # Tauri 2.0 窗口安全能力（Capabilities / ACL）声明文件
├── bin/
│   └── metis-engine-*     # PyInstaller 编译生成的 Python Sidecar 独立可执行文件存放目录
├── icons/
│   ├── icon.ico           # Windows 应用与安装程序主图标
│   ├── icon.icns          # macOS 应用 Bundle 主图标
│   ├── icon.png           # 通用高分辨率主图标（亦用于在运行时内联设置窗口图标）
│   ├── 32x32.png          # 标准小尺寸 PNG 图标
│   ├── 128x128.png        # 标准中等尺寸 PNG 图标
│   ├── 128x128@2x.png     # Retina 2x 高清 PNG 图标 (256x256)
│   ├── Square*Logo.png    # Windows AppX / MSIX 磁贴各尺寸视觉图标集合
│   └── StoreLogo.png      # Microsoft Store 商店徽标图标
├── gen/
│   └── schemas/           # Tauri 构建时自动生成的配置与权限 JSON Schema 校验文件（Git 忽略）
├── build.rs               # Cargo 编译前置脚本（自动创建 Sidecar 占位文件及注入 Windows 图标资源）
├── Cargo.toml             # Rust Crate 包清单、元信息与依赖配置
├── Cargo.lock             # Rust 依赖树精确版本锁定文件
├── tauri.conf.json        # Tauri 2.0 窗口属性、前端静态资源路径与跨平台打包配置
├── .gitignore             # Git 忽略规则（忽略编译产物 /target/ 与自动生成的 /gen/schemas）
├── README.md              # Tauri 宿主外壳英文架构说明文档
└── summary.md             # 本文件：详细注明目录下每个文件与子目录的职责与实现细节
```

---

## 各文件与目录详细用途说明

### 1. [`src/main.rs`](src/main.rs) — 桌面应用二进制入口
Rust 可执行文件（Binary Crate）的极简启动入口。

* **主要职责**：
  * `#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]`：在 Release 构建模式下，将 Windows 子系统设为 `"windows"`，屏蔽启动时的黑色命令行控制台窗口（Console Window）。
  * 调用库模块入口函数 `metis_lib::run()` 启动 Tauri 应用运行时。

---

### 2. [`src/lib.rs`](src/lib.rs) — 宿主核心库、Sidecar 桥接器与指令处理器
实现 Tauri 宿主进程的全部核心逻辑，包含 Python Sidecar 进程管理（`SidecarBridge`）、Tauri Invoke 命令处理（`commands::host_post`）以及应用与窗口事件监听（`run`）。

* **主要职责与核心组件**：
  * **`SidecarBridge`（Python 子进程监督器与 IPC 管道桥接）**：
    * **智能路径解析 (`resolve_sidecar_command`)**：
      * 在生产环境（且未设置 `METIS_DEV_PYTHON=1`）下，按优先级检索可执行文件同级目录与 `src-tauri/bin/` 下是否存在真实打包的 Sidecar 二进制文件（如 `metis-engine.exe`、`metis-engine-x86_64-pc-windows-msvc.exe`、`metis-engine-x86_64-pc-windows-gnu.exe`、`metis-engine-aarch64-apple-darwin`、`metis-engine-x86_64-unknown-linux-gnu` 等，且文件大小 `> 1024` 字节以排除空占位符）。
      * 在开发环境或未找到预编译二进制时，自动回退查找 `src-python/main.py`，并调用系统 Python 解释器（支持通过 `METIS_PYTHON_BIN` 环境变量指定，Windows 默认 `python`，Unix 默认 `python3`）以无缓冲模式 `python -u src-python/main.py` 启动。
    * **进程启动与流监听 (`build_tokio_command`, `start`)**：
      * 为子进程注入 `PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1` 及当前应用版本号 `METIS_APP_VERSION`（取自编译期 `CARGO_PKG_VERSION`）；在 Windows 平台附加 `CREATE_NO_WINDOW` (`0x0800_0000`) 隐藏子进程黑窗。
      * 建立异步 `stdin`、`stdout`、`stderr` 管道，并派生两个后台 Tokio 异步任务：
        1. 持续按行读取 `stdout`：识别以 `METIS_IPC:` 开头的 JSON 负载，反序列化后通过 `app_handle.emit("host_message", val)` 实时推送给前端 WebView；非 IPC 普通输出则记录到 `log::info!`。
        2. 持续按行读取 `stderr`：将错误日志转发至 `log::warn!`。
    * **向 Sidecar 发送指令 (`send_json`)**：
      * 将 JSON 序列化为单行字符串（追加 `\n`），通过异步互斥锁写入子进程 `stdin` 并立即 `flush`。
    * **优雅关停 (`shutdown`)**：
      * 先主动释放并关闭子进程 `stdin` 句柄（使 Python 端立即收到 EOF 并主动退出），随后调用 `child.kill().await` 确保不会残留任何后台僵尸进程。
  * **`commands::host_post`（前端统一 Tauri Invoke 入口）**：
    * 接收前端发来的 JSON 字符串负载并根据 `action` 字段进行分流：
      * **原生无边框窗口控制**：`windowMin`（最小化）、`windowMax` / `windowToggleMaximize`（最大化/还原切换）、`windowClose`（先关闭 Sidecar 再关闭窗口）、`windowDrag`（触发原生窗口拖拽移动）。
      * **原生系统文件/文件夹选择器（基于 `rfd::AsyncFileDialog`）**：
        * `pickFiles`：打开多选文件对话框，选中后自动构造 `{"action": "addFiles", "paths": [...]}` 转发给 Python Sidecar。
        * `pickMedia`：打开带扩展名过滤（`png`, `jpg`, `jpeg`, `webp`, `gif`, `bmp`, `pdf`）的多模态媒体文件选择框并转发 `addFiles`。
        * `pickFolder`：打开文件夹选择框，选中后构造 `{"action": "addDirectory", "path": "..."}` 转发给 Python Sidecar。
      * **系统级文件定位与外部链接打开（基于 `open` crate）**：
        * `revealFile`：使用系统默认程序或资源管理器打开指定路径。
        * `openUrl`：安全校验 `http://`、`https://` 或 `mailto:` 前缀后调用系统默认浏览器打开外部链接。
      * **业务指令透传**：其余所有 Agent、会话、配置、白名单操作指令均直接通过 `state.send_json(&data)` 原样转发给 Python Sidecar。
  * **`run()`（Tauri 应用构建与窗口事件挂载）**：
    * 注册 `tauri_plugin_log` 日志插件与全局共享的 `Arc<SidecarBridge>` 状态。
    * 在 `setup` 钩子中：通过 `include_bytes!("../icons/icon.png")` 为主窗口设置运行时图标，向前端 WebView 注入全局变量 `window.__METIS_VERSION__`，并在后台异步预启动 Python Sidecar。
    * 在 `on_window_event` 钩子中：
      * 监听 `CloseRequested` 与 `Destroyed` 事件，触发 `SidecarBridge::shutdown()`。
      * 监听系统级文件拖拽释放事件 `DragDrop(DragDropEvent::Drop { paths, .. })`，将拖入窗口的文件路径列表自动转换为 `addFiles` 消息发往 Python Sidecar。

---

### 3. [`build.rs`](build.rs) — Cargo 构建前置脚本
在编译 Rust 代码之前由 Cargo 自动执行的构建脚本。

* **主要职责**：
  * **自动生成开发环境 Sidecar 占位文件**：由于 `tauri.conf.json` 配置了 `externalBin: ["bin/metis-engine"]`，`tauri_build` 在编译期会强制检查 `src-tauri/bin/metis-engine-<TARGET>[.exe]` 是否存在。`build.rs` 会在该文件不存在时自动创建一个 0 字节占位文件，使开发者无需先运行 PyInstaller 也能直接执行 `cargo check` 或 `cargo run`（运行时 `SidecarBridge` 会识别出 0 字节文件并自动回退到 `python -u src-python/main.py`）。而在正式打包发布时，`scripts/build_sidecar.py` 会用真实的 PyInstaller 产物覆盖该文件。
  * **Windows 图标资源注入**：在 Windows 平台下将 `icons/icon.ico` 复制到 `OUT_DIR` 并配置到 `tauri_build::WindowsAttributes`，确保编译出的 `.exe` 拥有正确的资源图标。

---

### 4. [`Cargo.toml`](Cargo.toml) & [`Cargo.lock`](Cargo.lock) — Rust 包清单与依赖锁定文件
* **[`Cargo.toml`](Cargo.toml)**：
  * 定义包名 `metis`、版本号（与 `package.json` 保持同步）、描述、MIT 许可证及源码仓库地址。
  * 声明库目标 `metis_lib`（`rlib`）。
  * 配置构建依赖 `tauri-build` 以及运行时依赖：
    * `tauri`（启用 `image-ico` 与 `image-png` 特性，用于运行时窗口图标加载）
    * `tauri-plugin-log`（日志管理）
    * `tokio`（启用 `full` 特性，提供异步进程管理、管道读写与并发任务调度）
    * `serde` / `serde_json`（JSON 序列化与 IPC 载荷解析）
    * `rfd`（Rusty File Dialogs，提供跨平台原生异步文件/目录选择对话框）
    * `open`（调用系统默认程序打开文件或外部 URL）
    * `log`（标准日志门面）
* **[`Cargo.lock`](Cargo.lock)**：
  * 自动生成并锁定所有直接与传递 Rust 依赖包的精确版本与校验和，保证跨机器与 CI/CD 构建的完全可复现性。

---

### 5. [`tauri.conf.json`](tauri.conf.json) — Tauri 2.0 应用与打包核心配置文件
定义桌面客户端的窗口形态、安全策略、前端资源路径以及跨平台安装程序打包规则。

* **主要配置项**：
  * **基础元数据**：产品名称 `Metis`，版本号动态引用 `../package.json`，应用标识符 `com.sakiko.metis`。
  * **前端路径 (`build.frontendDist`)**：直接指向 `../src-frontend`（原生 HTML/CSS/JS 前端目录，无需繁重的打包器）。
  * **窗口与全局 API (`app`)**：
    * `withGlobalTauri: true`：向前端暴露 `window.__TAURI__` 全局对象，方便无构建工具的原生前端直接调用 `invoke` 与 `listen`。
    * 主窗口 `main`：默认尺寸 `1100x760`（最小 `800x560`），设置 `decorations: false` 隐藏系统原生标题栏，以使用前端自定义的极简沉浸式标题栏。
  * **发布打包 (`bundle`)**：
    * 支持打包目标：`nsis`（Windows 安装包）、`deb` 与 `appimage`（Linux 安装包）、`dmg`（macOS 磁盘映像）。
    * `externalBin: ["bin/metis-engine"]`：声明将 `src-tauri/bin/` 下的 Python Sidecar 可执行文件随安装包一并分发。
    * 配置应用分类 `DeveloperTool`、长短描述及多平台图标路径。

---

### 6. [`capabilities/default.json`](capabilities/default.json) — Tauri 2.0 权限与安全能力配置
* 遵循 Tauri 2.0 的细粒度访问控制列表（ACL）安全模型。
* 为 `main` 主窗口授予 `core:default` 默认核心权限（允许前端与宿主进行基础事件监听、窗口状态查询与 `invoke` 通信）。

---

### 7. [`bin/`](bin/) — Sidecar 外部二进制存放目录
* 存放由 `scripts/build_sidecar.py`（调用 PyInstaller）编译生成的独立 Python Sidecar 可执行文件，命名遵循 Tauri 目标三元组规范：`metis-engine-<target_triple>[.exe]`（例如当前目录下的 `metis-engine-x86_64-pc-windows-gnu.exe`）。
* 在执行 `tauri build` 时，Tauri Bundler 会根据当前编译平台自动从该目录提取对应后缀的二进制文件并打包进最终的安装程序中。

---

### 8. [`icons/`](icons/) — 跨平台应用图标资源目录
存放由 `scripts/generate_mascot_icons.py` 生成的全套多尺寸、多格式平台图标：
* `icon.ico`：Windows 可执行文件与 NSIS 安装程序图标。
* `icon.icns`：macOS `.app` 与 `.dmg` 图标。
* `icon.png` / `32x32.png` / `128x128.png` / `128x128@2x.png`：Linux 桌面环境图标及窗口内联加载图标。
* `Square30x30Logo.png` ~ `Square310x310Logo.png` & `StoreLogo.png`：Windows 现代应用磁贴与商店规范图标。

---

### 9. [`gen/schemas/`](gen/schemas/) — 自动生成的配置 Schema 目录
* 包含 Tauri 构建脚本自动生成的 JSON Schema 文件（`acl-manifests.json`、`capabilities.json`、`desktop-schema.json`、`windows-schema.json`），用于为 `tauri.conf.json` 和 `capabilities/default.json` 提供 IDE 智能提示与语法校验。
* 该目录属于构建生成物，已在 `.gitignore` 中配置忽略。

---

### 10. [`.gitignore`](.gitignore) & [`README.md`](README.md)
* **[`.gitignore`](.gitignore)**：忽略 Rust 编译产物目录 `/target/` 与自动生成的 Schema 目录 `/gen/schemas`。
* **[`README.md`](README.md)**：面向开发者的英文模块说明文档，概述 Tauri 2.0 宿主外壳职责与 Sidecar 生命周期管理机制。
