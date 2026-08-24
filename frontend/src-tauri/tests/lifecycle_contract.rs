use sage_desktop_lib::lifecycle::{
    can_clean_orphan, lifecycle_action, single_instance_action, startup_action, CrashBudget,
    LifecycleAction, LifecycleEvent, ObservedProcess, OrphanRecord, SingleInstanceAction,
    StartupAction,
};

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
    assert_eq!(
        single_instance_action(),
        SingleInstanceAction::ShowAndFocusMainWindow
    );
}

#[test]
fn lifecycle_matrix_keeps_sidecar_for_hide_and_disconnect() {
    assert_eq!(
        lifecycle_action(LifecycleEvent::WindowHidden),
        LifecycleAction::KeepRunning
    );
    assert_eq!(
        lifecycle_action(LifecycleEvent::ConnectionLost),
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
