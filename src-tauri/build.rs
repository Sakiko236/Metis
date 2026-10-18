fn main() {
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
