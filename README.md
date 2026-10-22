<div align="center">
  <img src="assets/metis-icon.png" width="108" height="108" alt="Metis Logo" />
  <br/>
  <h3> Metis Agent </h3>
  <p><b>File-Centric Micro Coding Agent</b></p>

  [![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-0078D6)](https://github.com/Sakiko236/Metis)
  [![Architecture](https://img.shields.io/badge/Architecture-Tauri%202.0%20%2B%20Python%20Sidecar-blue?logo=tauri&logoColor=white)](https://tauri.app)
  [![Python](https://img.shields.io/badge/Engine-Python%203.10%2B-yellow?logo=python&logoColor=white)](https://python.org)
  [![License](https://img.shields.io/badge/License-MIT-emerald)](LICENSE)
</div>

---

## 🖼️ Interface Preview

<div align="center">
  <p><b>Clean, minimalist workspace and file management</b></p>
  <img src="assets/001.png" alt="Metis Minimalist Main Interface" width="850" />
</div>

---

## 💡 Philosophy & Design

> **This could be the most useless agent in the world, but I like it.**

I love using web chat interfaces for LLMs because I really enjoy solving problems through conversation. The only drawback is that I have to manually upload and download code every time. However, I’m not really into heavy harnesses either—they just end up making me feel foolish.

---

## ✨ Key Features

Technically speaking, it's pretty much just a chat LLM that can modify local files, but with full ReAct capabilities under the hood. After some thought, I decided to drop terminal execution support in v1.5.0. If you still need command-line execution, though, feel free to grab v1.4.12.

Another mind-blowing detail: because I built the UI with Rust, it’s ridiculously small. The installer and memory footprint are both around just **10MB**! It also launches in the blink of an eye.

## 🖥️ Quick Start & Downloads

Download the appropriate release package for your operating system from the [Releases](https://github.com/Sakiko236/Metis/releases) page:

* **🪟 Windows**:
  * **Installer**: `Metis_<version>_x64-setup.exe` (NSIS installer with bundled standalone Python engine & clean uninstaller)
* **🍎 macOS**:
  * **Apple Silicon (M1/M2/M3/M4)**: `Metis_<version>_aarch64.dmg`
* **🐧 Linux**:
  * **Debian / Ubuntu**: `metis_<version>_amd64.deb`
  * **Universal Linux**: `Metis_<version>_amd64.AppImage`

---

<div align="center">
  <sub>Metis · 2026</sub>
</div>
