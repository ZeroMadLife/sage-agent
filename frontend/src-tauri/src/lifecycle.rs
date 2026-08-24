use serde::{Deserialize, Serialize};
use std::path::PathBuf;

const WINDOW_SECONDS: u64 = 10 * 60;
const BACKOFF_SECONDS: [u64; 3] = [1, 2, 4];

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct CrashBudget {
    crash_timestamps: Vec<u64>,
}

impl CrashBudget {
    pub fn record_crash(&mut self, now: u64) -> Option<u64> {
        self.prune(now);
        self.crash_timestamps.push(now);
        BACKOFF_SECONDS
            .get(self.crash_timestamps.len() - 1)
            .copied()
    }

    pub fn is_circuit_open(&mut self, now: u64) -> bool {
        self.prune(now);
        self.crash_timestamps.len() > BACKOFF_SECONDS.len()
    }

    fn prune(&mut self, now: u64) {
        let cutoff = now.saturating_sub(WINDOW_SECONDS);
        self.crash_timestamps
            .retain(|timestamp| *timestamp >= cutoff);
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LifecycleEvent {
    WindowHidden,
    ConnectionLost,
    SidecarCrash,
    ExplicitExit,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LifecycleAction {
    KeepRunning,
    RestartWithBackoff,
    GracefulStop,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SingleInstanceAction {
    ShowAndFocusMainWindow,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StartupAction {
    Launch,
    Blocked,
}

pub fn startup_action(crash_budget: &mut CrashBudget, now: u64) -> StartupAction {
    if crash_budget.is_circuit_open(now) {
        StartupAction::Blocked
    } else {
        StartupAction::Launch
    }
}

pub fn single_instance_action() -> SingleInstanceAction {
    SingleInstanceAction::ShowAndFocusMainWindow
}

pub fn lifecycle_action(event: LifecycleEvent) -> LifecycleAction {
    match event {
        LifecycleEvent::WindowHidden | LifecycleEvent::ConnectionLost => {
            LifecycleAction::KeepRunning
        }
        LifecycleEvent::SidecarCrash => LifecycleAction::RestartWithBackoff,
        LifecycleEvent::ExplicitExit => LifecycleAction::GracefulStop,
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct OrphanRecord {
    pub pid: u32,
    pub start_time: u64,
    pub executable: PathBuf,
}

#[derive(Debug, Clone)]
pub struct ObservedProcess {
    pub pid: u32,
    pub start_time: u64,
    pub executable: PathBuf,
}

pub fn can_clean_orphan(record: &OrphanRecord, observed: &ObservedProcess) -> bool {
    record.pid == observed.pid
        && record.start_time == observed.start_time
        && record.executable == observed.executable
        && record.executable.is_absolute()
}
