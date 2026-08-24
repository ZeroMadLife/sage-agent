fn main() {
    if let Ok(output) = std::process::Command::new("git")
        .args(["rev-parse", "HEAD"])
        .output()
    {
        if output.status.success() {
            let sha = String::from_utf8_lossy(&output.stdout);
            println!("cargo:rustc-env=SAGE_BUILD_SHA={}", sha.trim());
        }
    }
    tauri_build::build()
}
