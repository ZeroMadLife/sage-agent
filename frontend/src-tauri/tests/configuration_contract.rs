use serde_json::Value;
use std::fs;
use std::path::PathBuf;

fn crate_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn repository_root() -> PathBuf {
    crate_root().join("../..").canonicalize().unwrap()
}

#[test]
fn production_bundle_has_one_fixed_sidecar_and_no_remote_navigation() {
    let config: Value = serde_json::from_slice(
        &fs::read(crate_root().join("tauri.conf.json")).expect("tauri config must exist"),
    )
    .expect("tauri config must be JSON");

    assert_eq!(
        config.pointer("/bundle/externalBin").unwrap(),
        &serde_json::json!(["binaries/sage-api"])
    );
    assert_eq!(
        config.pointer("/bundle/resources/binaries~1sidecar"),
        Some(&serde_json::json!("sidecar"))
    );
    assert_eq!(
        config
            .pointer("/app/windows/0/devtools")
            .and_then(Value::as_bool),
        Some(false)
    );
    let csp = config
        .pointer("/app/security/csp")
        .and_then(Value::as_str)
        .unwrap();
    assert!(csp.starts_with("default-src 'self'"));
    assert!(!csp.contains("https://*"));
    assert!(!csp.contains("http://*"));
}

#[test]
fn launcher_execs_only_the_fixed_bundle_relative_sidecar() {
    let launcher = fs::read_to_string(crate_root().join("launcher/sage-sidecar-launcher.c"))
        .expect("launcher source must exist");
    assert!(launcher.contains("../Resources/sidecar/sage-api-aarch64-apple-darwin"));
    assert!(launcher.contains("execv(sidecar, sidecar_argv)"));
    assert!(!launcher.contains("getenv("));
    assert!(!launcher.contains("system("));
}

#[test]
fn main_window_capability_does_not_expose_shell_or_filesystem() {
    let capability: Value = serde_json::from_slice(
        &fs::read(crate_root().join("capabilities/main.json")).expect("main capability must exist"),
    )
    .expect("main capability must be JSON");
    assert_eq!(
        capability.get("permissions").unwrap(),
        &serde_json::json!(["dialog:allow-open"])
    );
    let serialized = capability.to_string();
    assert!(!serialized.contains("shell"));
    assert!(!serialized.contains("fs:"));
}

#[test]
fn desktop_host_registers_the_directory_dialog_plugin() {
    let source = fs::read_to_string(crate_root().join("src/lib.rs"))
        .expect("desktop host source must exist");
    assert!(source.contains("tauri_plugin_dialog::init()"));
}

#[test]
fn release_build_identity_is_explicit_and_cache_sensitive() {
    let build_script =
        fs::read_to_string(crate_root().join("build.rs")).expect("desktop build script must exist");

    assert!(build_script.contains("cargo:rerun-if-env-changed=SAGE_BUILD_SHA"));
    assert!(build_script.contains("std::env::var(\"SAGE_BUILD_SHA\")"));
    assert!(!build_script.contains("Command::new(\"git\")"));
}

#[test]
fn desktop_ci_and_manifest_pin_the_supported_arm64_rust_toolchain() {
    let workflow =
        fs::read_to_string(repository_root().join(".github/workflows/desktop-quality.yml"))
            .expect("desktop workflow must exist");
    let manifest =
        fs::read_to_string(crate_root().join("Cargo.toml")).expect("Cargo manifest must exist");
    let toolchain = fs::read_to_string(crate_root().join("rust-toolchain.toml"))
        .expect("desktop Rust toolchain must exist");

    assert!(workflow.contains("runs-on: macos-15"));
    assert!(!workflow.contains("runs-on: macos-14"));
    assert!(workflow.contains("dtolnay/rust-toolchain@1.98.0"));
    assert!(manifest.contains("rust-version = \"1.98\""));
    assert!(toolchain.contains("channel = \"1.98.0\""));
    assert!(toolchain.contains("targets = [\"aarch64-apple-darwin\"]"));
}

#[test]
fn registered_commands_are_the_five_fixed_desktop_actions() {
    let source = fs::read_to_string(crate_root().join("src/lib.rs"))
        .expect("desktop host source must exist");
    let compact: String = source
        .chars()
        .filter(|value| !value.is_whitespace())
        .collect();

    assert!(compact.contains(
        "tauri::generate_handler![desktop_host_status,desktop_exit,desktop_open_diagnostics,desktop_onboarding_status,desktop_onboarding_action]"
    ));
    assert!(compact.contains("manage(SharedOnboardingState::default())"));
    assert!(compact.contains("onboarding::initialize(app.handle().clone())"));
    assert!(compact.contains("RunEvent::Exit=event"));
    assert!(compact.contains("supervisor::finalize_exit(state)"));
    assert!(!source.contains("core:default"));
}
