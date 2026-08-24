fn main() {
    println!("cargo:rerun-if-env-changed=SAGE_BUILD_SHA");
    let profile = std::env::var("PROFILE").unwrap_or_default();
    let sha = std::env::var("SAGE_BUILD_SHA").unwrap_or_else(|_| {
        if profile == "debug" {
            "dev".into()
        } else {
            panic!("release desktop builds require SAGE_BUILD_SHA")
        }
    });
    if sha != "dev" || profile != "debug" {
        assert!(
            sha.len() == 40 && sha.bytes().all(|value| value.is_ascii_hexdigit()),
            "SAGE_BUILD_SHA must be a 40-character Git SHA"
        );
    }
    println!("cargo:rustc-env=SAGE_BUILD_SHA={sha}");
    tauri_build::build()
}
