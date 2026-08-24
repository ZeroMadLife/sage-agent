use sage_desktop_lib::lifecycle::{
    apply_single_instance_action, atomic_write_private, can_clean_orphan, lifecycle_action,
    single_instance_action, startup_action, terminate_verified, CrashBudget, LifecycleAction,
    LifecycleEvent, ObservedProcess, OrphanRecord, ProcessSignal, SingleInstanceAction,
    StartupAction, TerminationOutcome,
};
use std::cell::RefCell;
use std::fs;

#[test]
fn crash_budget_allows_three_restarts_in_ten_minutes_then_blocks() {
    let mut budget = CrashBudget::default();
    assert_eq!(budget.record_crash(0), Some(1));
    assert_eq!(budget.record_crash(10), Some(2));
    assert_eq!(budget.record_crash(20), Some(4));
    assert_eq!(budget.record_crash(30), None);
    assert!(budget.is_circuit_open(30));
    assert_eq!(budget.record_crash(631), Some(1));
}

#[test]
fn persisted_open_circuit_remains_blocked_after_host_restart() {
    let mut budget = CrashBudget::default();
    for timestamp in [0, 10, 20, 30] {
        budget.record_crash(timestamp);
    }

    assert_eq!(startup_action(&mut budget, 31), StartupAction::Blocked);
    assert_eq!(startup_action(&mut budget, 631), StartupAction::Launch);
}

#[test]
fn second_instance_only_reveals_the_existing_main_window() {
    let action = single_instance_action();
    let effects = RefCell::new(Vec::new());

    apply_single_instance_action(
        action,
        || effects.borrow_mut().push("show"),
        || effects.borrow_mut().push("focus"),
    );

    assert_eq!(action, SingleInstanceAction::ShowAndFocusMainWindow);
    assert_eq!(*effects.borrow(), ["show", "focus"]);
}

#[test]
fn lifecycle_matrix_keeps_sidecar_for_hide_and_restarts_real_crashes() {
    assert_eq!(
        lifecycle_action(LifecycleEvent::WindowHidden),
        LifecycleAction::KeepRunning
    );
    assert_eq!(
        lifecycle_action(LifecycleEvent::SidecarCrash),
        LifecycleAction::RestartWithBackoff
    );
    assert_eq!(
        lifecycle_action(LifecycleEvent::ExplicitExit),
        LifecycleAction::GracefulStop
    );
}

#[test]
fn grace_period_identity_change_never_reaches_sigkill() {
    let record = OrphanRecord {
        pid: 42,
        start_time: 100,
        executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
    };
    let matching = ObservedProcess {
        pid: 42,
        start_time: 100,
        executable: record.executable.clone(),
    };
    let changed = ObservedProcess {
        pid: 42,
        start_time: 101,
        executable: record.executable.clone(),
    };
    let mut observations = [Some(matching.clone()), Some(matching), Some(changed)].into_iter();
    let mut signals = Vec::new();

    let outcome = terminate_verified(
        &record,
        || observations.next().flatten(),
        |signal| {
            signals.push(signal);
            Ok(())
        },
        || {},
        5,
    )
    .unwrap();

    assert_eq!(outcome, TerminationOutcome::IdentityChanged);
    assert_eq!(signals, vec![ProcessSignal::Term]);
}

#[test]
fn sigkill_path_waits_until_the_same_process_is_gone() {
    let record = OrphanRecord {
        pid: 42,
        start_time: 100,
        executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
    };
    let matching = ObservedProcess {
        pid: 42,
        start_time: 100,
        executable: record.executable.clone(),
    };
    let mut observations = [
        Some(matching.clone()),
        Some(matching.clone()),
        Some(matching.clone()),
        Some(matching.clone()),
        None,
    ]
    .into_iter();
    let mut signals = Vec::new();

    let outcome = terminate_verified(
        &record,
        || observations.next().flatten(),
        |signal| {
            signals.push(signal);
            Ok(())
        },
        || {},
        1,
    )
    .unwrap();

    assert_eq!(outcome, TerminationOutcome::KillSent);
    assert_eq!(signals, vec![ProcessSignal::Term, ProcessSignal::Kill]);
}

#[test]
fn signal_failure_is_reported_without_claiming_the_process_stopped() {
    let record = OrphanRecord {
        pid: 42,
        start_time: 100,
        executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
    };
    let matching = ObservedProcess {
        pid: 42,
        start_time: 100,
        executable: record.executable.clone(),
    };

    let error = terminate_verified(
        &record,
        || Some(matching.clone()),
        |_| Err(std::io::Error::from(std::io::ErrorKind::PermissionDenied)),
        || {},
        1,
    )
    .unwrap_err();

    assert_eq!(error.kind(), std::io::ErrorKind::PermissionDenied);
}

#[test]
fn process_still_alive_after_sigkill_is_reported_as_timeout() {
    let record = OrphanRecord {
        pid: 42,
        start_time: 100,
        executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
    };
    let matching = ObservedProcess {
        pid: 42,
        start_time: 100,
        executable: record.executable.clone(),
    };
    let mut signals = Vec::new();

    let error = terminate_verified(
        &record,
        || Some(matching.clone()),
        |signal| {
            signals.push(signal);
            Ok(())
        },
        || {},
        1,
    )
    .unwrap_err();

    assert_eq!(error.kind(), std::io::ErrorKind::TimedOut);
    assert_eq!(signals, [ProcessSignal::Term, ProcessSignal::Kill]);
}

#[test]
fn private_state_write_reports_write_and_rename_failures() {
    let root = tempfile::tempdir().unwrap();
    let blocked_parent = root.path().join("not-a-directory");
    fs::write(&blocked_parent, b"file").unwrap();
    assert!(atomic_write_private(&blocked_parent.join("state.json"), b"{}").is_err());

    let target = root.path().join("state.json");
    fs::create_dir(&target).unwrap();
    assert!(atomic_write_private(&target, b"{}").is_err());
}

#[test]
fn orphan_cleanup_requires_pid_start_time_and_exact_executable() {
    let record = OrphanRecord {
        pid: 42,
        start_time: 100,
        executable: "/Applications/Sage.app/Contents/MacOS/sage-api".into(),
    };
    let observed = ObservedProcess {
        pid: 42,
        start_time: 100,
        executable: "/Applications/Sage.app/Contents/MacOS/sage-api".into(),
    };
    assert!(can_clean_orphan(&record, &observed));

    let mut reused_pid = observed.clone();
    reused_pid.start_time = 101;
    assert!(!can_clean_orphan(&record, &reused_pid));
    let mut wrong_binary = observed;
    wrong_binary.executable = "/bin/sh".into();
    assert!(!can_clean_orphan(&record, &wrong_binary));
}
