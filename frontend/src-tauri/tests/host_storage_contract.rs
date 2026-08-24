use sage_desktop_lib::diagnostics::DiagnosticLog;
use sage_desktop_lib::lifecycle::{CrashBudget, OrphanRecord};
use sage_desktop_lib::state_repository::{DesktopStateRepository, HostDiskState};

#[test]
fn desktop_state_repository_round_trips_crash_budget_and_orphan() {
    let root = tempfile::tempdir().unwrap();
    let repository = DesktopStateRepository::new(root.path().join("desktop-host-state.json"));
    let mut crash_budget = CrashBudget::default();
    crash_budget.record_crash(10);
    let state = HostDiskState {
        crash_budget,
        orphan: Some(OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        }),
    };

    repository.save(&state).unwrap();

    let mut restored = repository.load();
    assert_eq!(restored.orphan, state.orphan);
    assert_eq!(restored.crash_budget.record_crash(20), Some(2));
}

#[test]
fn diagnostic_log_serializes_only_the_public_allowlist() {
    let root = tempfile::tempdir().unwrap();
    let log = DiagnosticLog::create(root.path().join("diagnostics")).unwrap();

    log.append_at(100, "sidecar_crash", "degraded", "desktop_sidecar_crashed")
        .unwrap();

    let payload: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(root.path().join("diagnostics/desktop-host.jsonl")).unwrap(),
    )
    .unwrap();
    let keys = payload
        .as_object()
        .unwrap()
        .keys()
        .cloned()
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(
        keys,
        ["event", "reason_code", "state", "timestamp"]
            .into_iter()
            .map(str::to_string)
            .collect()
    );
    assert_eq!(payload["timestamp"], 100);
}
