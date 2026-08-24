use sage_desktop_lib::diagnostics::DiagnosticLog;
use sage_desktop_lib::lifecycle::{CrashBudget, OrphanRecord};
use sage_desktop_lib::state_repository::{DesktopStateRepository, HostDiskState};
use std::sync::{Arc, Barrier};

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
fn unpublished_orphan_journal_round_trips_deduplicates_and_replaces() {
    let root = tempfile::tempdir().unwrap();
    let repository = DesktopStateRepository::new(root.path().join("desktop-host-state.json"));
    let first = OrphanRecord {
        pid: 42,
        start_time: 100,
        executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
    };
    let second = OrphanRecord {
        pid: 43,
        start_time: 101,
        executable: first.executable.clone(),
    };

    repository.add_unpublished_orphan(&first).unwrap();
    repository.add_unpublished_orphan(&first).unwrap();
    repository.add_unpublished_orphan(&second).unwrap();
    assert_eq!(
        repository.load_unpublished_orphans().unwrap(),
        [first.clone(), second.clone()]
    );

    repository
        .replace_unpublished_orphans(std::slice::from_ref(&second))
        .unwrap();
    assert_eq!(repository.load_unpublished_orphans().unwrap(), [second]);
}

#[test]
fn unpublished_orphan_remove_is_exact_and_durable() {
    let root = tempfile::tempdir().unwrap();
    let repository = DesktopStateRepository::new(root.path().join("desktop-host-state.json"));
    let record = OrphanRecord {
        pid: 42,
        start_time: 100,
        executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
    };
    repository.add_unpublished_orphan(&record).unwrap();

    repository.remove_unpublished_orphan(&record).unwrap();

    assert_eq!(repository.load_unpublished_orphans().unwrap(), []);
    assert!(repository.remove_unpublished_orphan(&record).is_err());
}

#[test]
fn corrupt_unpublished_orphan_journal_fails_closed() {
    let root = tempfile::tempdir().unwrap();
    let repository = DesktopStateRepository::new(root.path().join("desktop-host-state.json"));
    std::fs::write(repository.unpublished_path(), b"not-json").unwrap();

    assert!(repository.load_unpublished_orphans().is_err());
    assert!(repository
        .add_unpublished_orphan(&OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        })
        .is_err());
}

#[test]
fn concurrent_unpublished_orphan_appends_do_not_lose_unique_records() {
    let root = tempfile::tempdir().unwrap();
    let repository = DesktopStateRepository::new(root.path().join("desktop-host-state.json"));
    let barrier = Arc::new(Barrier::new(9));
    let mut workers = Vec::new();
    for pid in 42..50 {
        let repository = repository.clone();
        let barrier = barrier.clone();
        workers.push(std::thread::spawn(move || {
            barrier.wait();
            repository
                .add_unpublished_orphan(&OrphanRecord {
                    pid,
                    start_time: u64::from(pid) + 100,
                    executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
                })
                .unwrap();
        }));
    }
    barrier.wait();
    for worker in workers {
        worker.join().unwrap();
    }

    let mut pids = repository
        .load_unpublished_orphans()
        .unwrap()
        .into_iter()
        .map(|record| record.pid)
        .collect::<Vec<_>>();
    pids.sort_unstable();
    assert_eq!(pids, (42..50).collect::<Vec<_>>());
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
