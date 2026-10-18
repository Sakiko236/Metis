fn main() {
    // Ensure `bin/metis-engine-<target>` exists for `tauri_build` during dev `cargo check`/`cargo run`.
    // In production release builds, `scripts/build_sidecar.py` overwrites this with the real PyInstaller binary.
    if let Ok(target) = std::env::var("TARGET") {
        let bin_dir = std::path::Path::new("bin");
        let _ = std::fs::create_dir_all(bin_dir);
        let ext = if target.contains("windows") { ".exe" } else { "" };
        let sidecar_path = bin_dir.join(format!("metis-engine-{}{}", target, ext));
        if !sidecar_path.exists() {
            let _ = std::fs::write(&sidecar_path, b"");
        }
    }

    let mut attrs = tauri_build::Attributes::new();
    #[cfg(windows)]
    {
        let local_icon = std::path::Path::new("icons/icon.ico");
        if local_icon.exists() {
            if let Ok(out_dir) = std::env::var("OUT_DIR") {
                let target_icon = std::path::Path::new(&out_dir).join("icon.ico");
                let _ = std::fs::copy(local_icon, &target_icon);
                if target_icon.exists() {
                    let win_attrs = tauri_build::WindowsAttributes::new().window_icon_path(&target_icon);
                    attrs = attrs.windows_attributes(win_attrs);
                }
            }
        }
    }
    tauri_build::try_build(attrs).expect("failed to run build script");
}
