use serde_json::Value;
use std::fs;
use std::path::PathBuf;

fn crate_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
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
        &serde_json::json!([])
    );
    let serialized = capability.to_string();
    assert!(!serialized.contains("shell"));
    assert!(!serialized.contains("fs:"));
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
fn registered_commands_are_the_three_fixed_desktop_host_actions() {
    let source = fs::read_to_string(crate_root().join("src/lib.rs"))
        .expect("desktop host source must exist");
    let compact: String = source
        .chars()
        .filter(|value| !value.is_whitespace())
        .collect();

    assert!(compact.contains(
        "tauri::generate_handler![desktop_host_status,desktop_exit,desktop_open_diagnostics]"
    ));
    assert!(compact.contains("RunEvent::Exit=event"));
    assert!(compact.contains("supervisor::finalize_exit(state)"));
    assert!(!source.contains("core:default"));
}
