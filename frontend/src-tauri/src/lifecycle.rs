use serde::{Deserialize, Serialize};
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
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

pub fn apply_single_instance_action<Show, Focus>(
    action: SingleInstanceAction,
    mut show: Show,
    mut focus: Focus,
) where
    Show: FnMut(),
    Focus: FnMut(),
{
    match action {
        SingleInstanceAction::ShowAndFocusMainWindow => {
            show();
            focus();
        }
    }
}

pub fn lifecycle_action(event: LifecycleEvent) -> LifecycleAction {
    match event {
        LifecycleEvent::WindowHidden => LifecycleAction::KeepRunning,
        LifecycleEvent::SidecarCrash => LifecycleAction::RestartWithBackoff,
        LifecycleEvent::ExplicitExit => LifecycleAction::GracefulStop,
    }
}

#[derive(Debug, Clone, Deserialize, Eq, PartialEq, Serialize)]
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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProcessSignal {
    Term,
    Kill,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TerminationOutcome {
    Stopped,
    IdentityChanged,
    KillSent,
}

pub fn terminate_verified<Observe, SendSignal, Wait>(
    record: &OrphanRecord,
    mut observe: Observe,
    mut send_signal: SendSignal,
    mut wait: Wait,
    grace_checks: usize,
) -> io::Result<TerminationOutcome>
where
    Observe: FnMut() -> Option<ObservedProcess>,
    SendSignal: FnMut(ProcessSignal) -> io::Result<()>,
    Wait: FnMut(),
{
    let Some(observed) = observe() else {
        return Ok(TerminationOutcome::Stopped);
    };
    if !can_clean_orphan(record, &observed) {
        return Ok(TerminationOutcome::IdentityChanged);
    }
    send_signal(ProcessSignal::Term)?;

    for _ in 0..=grace_checks {
        let Some(observed) = observe() else {
            return Ok(TerminationOutcome::Stopped);
        };
        if !can_clean_orphan(record, &observed) {
            return Ok(TerminationOutcome::IdentityChanged);
        }
        wait();
    }

    let Some(observed) = observe() else {
        return Ok(TerminationOutcome::Stopped);
    };
    if !can_clean_orphan(record, &observed) {
        return Ok(TerminationOutcome::IdentityChanged);
    }
    send_signal(ProcessSignal::Kill)?;
    for _ in 0..=grace_checks {
        let Some(observed) = observe() else {
            return Ok(TerminationOutcome::KillSent);
        };
        if !can_clean_orphan(record, &observed) {
            return Ok(TerminationOutcome::IdentityChanged);
        }
        wait();
    }
    Err(io::Error::new(
        io::ErrorKind::TimedOut,
        "verified process remained after SIGKILL",
    ))
}

pub fn atomic_write_private(path: &std::path::Path, bytes: &[u8]) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "state path has no parent"))?;
    fs::create_dir_all(parent)?;
    let temporary = path.with_extension("tmp");
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(&temporary)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(fs::Permissions::from_mode(0o600))?;
    }
    file.write_all(bytes)?;
    file.sync_all()?;
    fs::rename(&temporary, path)?;
    FileSync::sync_directory(parent)?;
    Ok(())
}

struct FileSync;

impl FileSync {
    fn sync_directory(path: &std::path::Path) -> io::Result<()> {
        fs::File::open(path)?.sync_all()
    }
}
