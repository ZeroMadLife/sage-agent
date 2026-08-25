use crate::diagnostics::DiagnosticLog;
use crate::lifecycle::{
    lifecycle_action, startup_action, terminate_verified, LifecycleAction, LifecycleEvent,
    ObservedProcess, OrphanRecord, ProcessSignal, StartupAction,
};
use crate::protocol::{validate_handshake, DesktopSession, ExpectedHandshake, Handshake};
use crate::state_repository::{DesktopStateRepository, HostDiskState};
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use rand::RngCore;
use serde::Serialize;
use serde_json::Value;
use std::fs;
use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use sysinfo::{Pid, ProcessesToUpdate, System};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use uuid::Uuid;
use zeroize::Zeroize;

const API_VERSION: &str = "1";
const PRODUCTION_ORIGIN: &str = "tauri://localhost";
const DEVELOPMENT_ORIGIN: &str = "http://127.0.0.1:5173";
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(15);
const HANDSHAKE_QUIET_PERIOD: Duration = Duration::from_millis(50);
const HEALTH_TIMEOUT: Duration = Duration::from_secs(15);
const EXIT_GRACE: Duration = Duration::from_secs(5);

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HostSnapshot {
    state: &'static str,
    reason_code: Option<&'static str>,
    action: Option<&'static str>,
    session: Option<DesktopSession>,
}

impl HostSnapshot {
    fn starting() -> Self {
        Self {
            state: "starting",
            reason_code: None,
            action: None,
            session: None,
        }
    }

    fn ready(session: DesktopSession) -> Self {
        Self {
            state: "ready",
            reason_code: None,
            action: None,
            session: Some(session),
        }
    }

    fn problem(state: &'static str, reason_code: &'static str, action: &'static str) -> Self {
        Self {
            state,
            reason_code: Some(reason_code),
            action: Some(action),
            session: None,
        }
    }

    fn persistence_failure() -> Self {
        Self::problem(
            "blocked",
            "desktop_state_persist_failed",
            "open_diagnostics",
        )
    }

    fn termination_failure() -> Self {
        Self::problem("blocked", "desktop_sidecar_stop_failed", "open_diagnostics")
    }

    fn unpublished_termination_failure() -> Self {
        Self::problem(
            "blocked",
            "desktop_unpublished_sidecar_cleanup_failed",
            "open_diagnostics",
        )
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum OwnershipRecoveryState {
    Healthy,
    Recovering,
    Untrusted,
    PersistFailed,
}

struct HostInner {
    snapshot: HostSnapshot,
    stopping: bool,
    pid: Option<u32>,
    child: Option<CommandChild>,
    repository: Option<DesktopStateRepository>,
    diagnostic_log: Option<DiagnosticLog>,
    disk: HostDiskState,
    unpublished_orphans: Vec<OrphanRecord>,
    ownership_recovery: OwnershipRecoveryState,
    snapshot_before_ownership_recovery: Option<HostSnapshot>,
    launch_generation: u64,
    configuration_restart_in_progress: bool,
    configuration_epoch: u64,
}

impl Default for HostInner {
    fn default() -> Self {
        Self {
            snapshot: HostSnapshot::starting(),
            stopping: false,
            pid: None,
            child: None,
            repository: None,
            diagnostic_log: None,
            disk: HostDiskState::default(),
            unpublished_orphans: Vec::new(),
            ownership_recovery: OwnershipRecoveryState::Healthy,
            snapshot_before_ownership_recovery: None,
            launch_generation: 0,
            configuration_restart_in_progress: false,
            configuration_epoch: 0,
        }
    }
}

#[derive(Clone, Default)]
pub struct SharedHostState(Arc<Mutex<HostInner>>);

impl SharedHostState {
    pub fn is_stopping(&self) -> bool {
        self.0.lock().expect("host state poisoned").stopping
    }
}

#[cfg(test)]
pub(crate) fn test_host_with_repository(repository: DesktopStateRepository) -> SharedHostState {
    let shared = SharedHostState::default();
    shared.0.lock().expect("host state poisoned").repository = Some(repository);
    shared
}

#[cfg(test)]
pub(crate) fn test_reserve_unpublished(shared: &SharedHostState, record: &OrphanRecord) -> bool {
    reserve_unpublished_launch_cleanup(shared, record)
}

#[cfg(test)]
pub fn configuration_action_failure(
    shared: &SharedHostState,
) -> Option<(&'static str, &'static str)> {
    let inner = shared.0.lock().expect("host state poisoned");
    ownership_admission_failure(&inner).map(|reason| (reason, "open_diagnostics"))
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct ConfigurationActionFailure {
    pub reason_code: &'static str,
    pub action: &'static str,
}

pub(crate) struct ConfigurationMutationGuard {
    shared: SharedHostState,
    epoch: u64,
}

pub(crate) struct ConfigurationMutationCommit<T> {
    pub value: T,
    pub restart: Option<ConfigurationRestartReceipt>,
}

#[derive(Debug)]
pub(crate) enum ConfigurationMutationCommitError<E> {
    Admission(ConfigurationActionFailure),
    Mutation(E),
}

pub(crate) struct ConfigurationRestartReceipt {
    request: ConfigurationRestartRequest,
}

pub(crate) fn acquire_configuration_mutation_guard(
    shared: &SharedHostState,
) -> Result<ConfigurationMutationGuard, ConfigurationActionFailure> {
    let inner = shared.0.lock().expect("host state poisoned");
    configuration_mutation_failure(&inner)?;
    Ok(ConfigurationMutationGuard {
        shared: shared.clone(),
        epoch: inner.configuration_epoch,
    })
}

impl ConfigurationMutationGuard {
    pub fn validate(&self) -> Result<(), ConfigurationActionFailure> {
        let inner = self.shared.0.lock().expect("host state poisoned");
        self.validate_locked(&inner)
    }

    pub fn commit<T, E, F>(
        &self,
        restart_required: bool,
        mutation: F,
    ) -> Result<ConfigurationMutationCommit<T>, ConfigurationMutationCommitError<E>>
    where
        F: FnOnce() -> Result<T, E>,
    {
        let mut inner = self.shared.0.lock().expect("host state poisoned");
        self.validate_locked(&inner)
            .map_err(ConfigurationMutationCommitError::Admission)?;
        let value = mutation().map_err(ConfigurationMutationCommitError::Mutation)?;
        let restart = restart_required.then(|| ConfigurationRestartReceipt {
            request: begin_configuration_restart_locked(&mut inner),
        });
        Ok(ConfigurationMutationCommit { value, restart })
    }

    fn validate_locked(&self, inner: &HostInner) -> Result<(), ConfigurationActionFailure> {
        if inner.configuration_epoch != self.epoch {
            return Err(configuration_failure(
                "desktop_configuration_superseded",
                "retry_provider_action",
            ));
        }
        configuration_mutation_failure(inner)
    }
}

fn configuration_mutation_failure(inner: &HostInner) -> Result<(), ConfigurationActionFailure> {
    if inner.stopping {
        return Err(configuration_failure("desktop_stopping", "restart_sage"));
    }
    if inner.configuration_restart_in_progress {
        return Err(configuration_failure(
            "desktop_configuration_restart_in_progress",
            "wait_for_startup",
        ));
    }
    if let Some(reason_code) = ownership_admission_failure(inner) {
        return Err(configuration_failure(reason_code, "open_diagnostics"));
    }
    Ok(())
}

fn configuration_failure(
    reason_code: &'static str,
    action: &'static str,
) -> ConfigurationActionFailure {
    ConfigurationActionFailure {
        reason_code,
        action,
    }
}

#[derive(Serialize)]
struct Bootstrap<'a> {
    instance_id: &'a str,
    nonce: &'a str,
    bearer: &'a str,
    origin: &'a str,
    data_dir: &'a Path,
    runtime: Option<BootstrapRuntime<'a>>,
}

#[derive(Serialize)]
struct BootstrapRuntime<'a> {
    workspace_path: &'a Path,
    provider: BootstrapProvider<'a>,
    sandbox_provider: &'a str,
    side_effect_tools_enabled: bool,
}

#[derive(Serialize)]
struct BootstrapProvider<'a> {
    provider_id: &'a str,
    base_url: &'a str,
    default_model: &'a str,
    api_mode: &'a str,
    api_key: &'a str,
}

#[tauri::command]
pub fn desktop_host_status(state: State<'_, SharedHostState>) -> HostSnapshot {
    state
        .0
        .lock()
        .expect("host state poisoned")
        .snapshot
        .clone()
}

#[tauri::command]
pub fn desktop_exit(app: AppHandle, state: State<'_, SharedHostState>) {
    request_exit(app, state.inner().clone());
}

#[tauri::command]
pub fn desktop_open_diagnostics(state: State<'_, SharedHostState>) -> Result<(), &'static str> {
    let diagnostics_dir = state
        .0
        .lock()
        .expect("host state poisoned")
        .diagnostic_log
        .as_ref()
        .map(|log| log.directory().to_path_buf())
        .ok_or("desktop_diagnostics_unavailable")?;
    std::process::Command::new("/usr/bin/open")
        .arg(diagnostics_dir)
        .spawn()
        .map_err(|_| "desktop_diagnostics_unavailable")?;
    Ok(())
}

pub fn request_exit(app: AppHandle, shared: SharedHostState) {
    if lifecycle_action(LifecycleEvent::ExplicitExit) != LifecycleAction::GracefulStop {
        return;
    }
    {
        let mut inner = shared.0.lock().expect("host state poisoned");
        if inner.stopping {
            return;
        }
        inner.stopping = true;
        inner.snapshot = HostSnapshot::problem("degraded", "desktop_stopping", "wait");
    }
    tauri::async_runtime::spawn(async move {
        stop_sidecar(&shared).await;
        app.exit(0);
    });
}

pub fn finalize_exit(shared: SharedHostState) {
    {
        let mut inner = shared.0.lock().expect("host state poisoned");
        inner.stopping = true;
    }
    stop_sidecar_blocking(&shared);
}

pub fn start(app: AppHandle) {
    let shared = app.state::<SharedHostState>().inner().clone();
    let data_dir = match app.path().app_data_dir() {
        Ok(path) => path,
        Err(_) => {
            set_problem(
                &shared,
                "blocked",
                "desktop_data_dir_unavailable",
                "restart_sage",
            );
            return;
        }
    };
    if fs::create_dir_all(&data_dir).is_err() {
        set_problem(
            &shared,
            "blocked",
            "desktop_data_dir_unavailable",
            "restart_sage",
        );
        return;
    }
    let repository = DesktopStateRepository::new(data_dir.join("desktop-host-state.json"));
    let diagnostic_log = match DiagnosticLog::create(data_dir.join("diagnostics")) {
        Ok(log) => log,
        Err(_) => {
            set_problem(
                &shared,
                "blocked",
                "desktop_diagnostics_unavailable",
                "restart_sage",
            );
            return;
        }
    };
    let mut disk = repository.load();
    let unpublished = reconcile_unpublished_startup(&repository, terminate_runtime);
    let cleanup_result = clean_known_orphan(&mut disk);
    let startup = startup_action(&mut disk.crash_budget, unix_seconds());
    let persisted = {
        let mut inner = shared.0.lock().expect("host state poisoned");
        inner.repository = Some(repository);
        inner.diagnostic_log = Some(diagnostic_log);
        inner.disk = disk;
        inner.unpublished_orphans = unpublished.records;
        inner.ownership_recovery = unpublished.health;
        persist_disk_locked(&inner)
    };
    if persisted.is_err()
        || matches!(
            unpublished.health,
            OwnershipRecoveryState::Untrusted | OwnershipRecoveryState::PersistFailed
        )
    {
        set_snapshot(&shared, HostSnapshot::persistence_failure());
        let _ = append_diagnostic(
            &shared,
            "state_persist_failed",
            "blocked",
            "desktop_state_persist_failed",
        );
        return;
    }
    if unpublished.cleanup_failed {
        set_snapshot(&shared, HostSnapshot::unpublished_termination_failure());
        let _ = append_diagnostic(
            &shared,
            "unpublished_sidecar_cleanup_failed",
            "blocked",
            "desktop_unpublished_sidecar_cleanup_failed",
        );
        return;
    }
    if cleanup_result.is_err() {
        set_snapshot(&shared, HostSnapshot::termination_failure());
        let _ = append_diagnostic(
            &shared,
            "orphan_cleanup_failed",
            "blocked",
            "desktop_sidecar_stop_failed",
        );
        return;
    }
    if startup == StartupAction::Blocked {
        set_problem(
            &shared,
            "blocked",
            "desktop_crash_budget_exhausted",
            "open_diagnostics",
        );
        let _ = append_diagnostic(
            &shared,
            "crash_budget_open",
            "blocked",
            "desktop_crash_budget_exhausted",
        );
        return;
    }
    let Some(generation) = next_launch_generation(&shared) else {
        return;
    };
    schedule_launch(app, shared, data_dir, Duration::ZERO, generation);
}

fn schedule_launch(
    app: AppHandle,
    shared: SharedHostState,
    data_dir: PathBuf,
    delay: Duration,
    generation: u64,
) {
    tauri::async_runtime::spawn(async move {
        if !delay.is_zero() {
            tokio::time::sleep(delay).await;
        }
        if launch_admission_failure(&shared, generation).is_some() {
            return;
        }
        match launch_once(&app, &shared, &data_dir, generation).await {
            Ok(receiver) => monitor(app, shared, data_dir, receiver, generation).await,
            Err(reason) if !shared.is_stopping() => {
                match record_launch_failure(&shared, generation, reason, unix_seconds()) {
                    LaunchFailureDisposition::RetryAfter(seconds) => schedule_launch(
                        app,
                        shared,
                        data_dir,
                        Duration::from_secs(seconds),
                        generation,
                    ),
                    LaunchFailureDisposition::RecoverableConfiguration => {
                        schedule_launch(app, shared, data_dir, Duration::from_secs(1), generation)
                    }
                    LaunchFailureDisposition::Blocked => {}
                    LaunchFailureDisposition::Superseded => {}
                }
            }
            Err(_) => {}
        }
    });
}

async fn launch_once(
    app: &AppHandle,
    shared: &SharedHostState,
    data_dir: &Path,
    generation: u64,
) -> Result<tauri::async_runtime::Receiver<CommandEvent>, &'static str> {
    transition_launch_to_starting(shared, generation, || {})?;
    let instance_id = Uuid::new_v4().to_string();
    let nonce = random_secret();
    let bearer = random_secret();
    let origin = runtime_origin();
    let runtime =
        crate::onboarding::runtime_configuration_for_app(app).map_err(|error| error.reason_code)?;
    let command = app
        .shell()
        .sidecar("sage-api")
        .map_err(|_| "desktop_sidecar_missing")?
        .args(["--desktop-host"]);
    let (mut receiver, mut child) = command
        .spawn()
        .map_err(|_| "desktop_sidecar_spawn_failed")?;
    let child_pid = child.pid();
    if shared.is_stopping() {
        let _ = child.kill();
        return Err("desktop_stopping");
    }
    let bootstrap = Bootstrap {
        instance_id: &instance_id,
        nonce: &nonce,
        bearer: &bearer,
        origin,
        data_dir,
        runtime: runtime.as_ref().map(|configuration| BootstrapRuntime {
            workspace_path: configuration.workspace_path(),
            provider: BootstrapProvider {
                provider_id: configuration.provider_id(),
                base_url: configuration.base_url(),
                default_model: configuration.default_model(),
                api_mode: configuration.api_mode(),
                api_key: configuration.expose_secret(),
            },
            sandbox_provider: configuration.sandbox_provider(),
            side_effect_tools_enabled: configuration.side_effect_tools_enabled(),
        }),
    };
    let mut encoded = serde_json::to_vec(&bootstrap).map_err(|_| "desktop_bootstrap_failed")?;
    encoded.push(b'\n');
    if child.write(&encoded).is_err() {
        encoded.zeroize();
        let _ = child.kill();
        return Err("desktop_bootstrap_failed");
    }
    encoded.zeroize();

    let handshake = match read_handshake(&mut receiver).await {
        Ok(value) => value,
        Err(reason) => {
            let _ = child.kill();
            return Err(reason);
        }
    };
    let expected = ExpectedHandshake {
        child_pid,
        instance_id: instance_id.clone(),
        nonce,
        api_version: API_VERSION.into(),
        build_sha: env!("SAGE_BUILD_SHA").into(),
    };
    let endpoint = match validate_handshake(&handshake, &expected) {
        Ok(value) => value,
        Err(error) => {
            let _ = child.kill();
            return Err(error.reason_code());
        }
    };
    if let Err(reason) = verify_handshake_quiet(&mut receiver).await {
        let _ = child.kill();
        return Err(reason);
    }
    if probe_health(handshake.port, &bearer, &expected.build_sha, origin).is_err() {
        let _ = child.kill();
        return Err("desktop_health_rejected");
    }
    let Some(observed) = observe_process(child_pid) else {
        let _ = child.kill();
        return Err("desktop_process_identity_unavailable");
    };
    let record = OrphanRecord {
        pid: observed.pid,
        start_time: observed.start_time,
        executable: observed.executable,
    };
    match commit_launch_success(
        shared,
        generation,
        record,
        DesktopSession {
            endpoint,
            bearer,
            instance_id,
        },
        child,
        |inner, child| inner.child = Some(child),
    ) {
        Ok(()) => Ok(receiver),
        Err((reason, record, child)) => {
            let termination = terminate_runtime_async(record.clone());
            Err(finalize_rejected_launch(shared, reason, record, child, termination).await)
        }
    }
}

fn transition_launch_to_starting<F>(
    shared: &SharedHostState,
    generation: u64,
    after_precheck: F,
) -> Result<(), &'static str>
where
    F: FnOnce(),
{
    if !is_current_generation(shared, generation) {
        return Err("desktop_launch_superseded");
    }
    after_precheck();
    let mut inner = shared.0.lock().expect("host state poisoned");
    if inner.stopping {
        return Err("desktop_stopping");
    }
    if inner.launch_generation != generation || inner.configuration_restart_in_progress {
        return Err("desktop_launch_superseded");
    }
    if let Some(reason) = ownership_admission_failure(&inner) {
        return Err(reason);
    }
    inner.snapshot = HostSnapshot::starting();
    Ok(())
}

fn commit_launch_success<T, F>(
    shared: &SharedHostState,
    generation: u64,
    record: OrphanRecord,
    session: DesktopSession,
    resource: T,
    publish_resource: F,
) -> Result<(), (&'static str, OrphanRecord, T)>
where
    F: FnOnce(&mut HostInner, T),
{
    let mut inner = shared.0.lock().expect("host state poisoned");
    if inner.stopping {
        return Err(("desktop_stopping", record, resource));
    }
    if inner.launch_generation == generation {
        if let Some(reason) = ownership_admission_failure(&inner) {
            return Err((reason, record, resource));
        }
    }
    if inner.launch_generation != generation
        || inner.configuration_restart_in_progress
        || inner.disk.orphan.is_some()
        || inner.pid.is_some()
        || inner.child.is_some()
    {
        return Err(("desktop_launch_superseded", record, resource));
    }

    inner.disk.orphan = Some(record.clone());
    // The repository write cannot re-enter host state. Ownership must be durable before
    // PID, child and ready become visible, so restart cannot split this commit.
    if persist_disk_locked(&inner).is_err() {
        inner.disk.orphan = None;
        return Err(("desktop_state_persist_failed", record, resource));
    }
    inner.pid = Some(record.pid);
    publish_resource(&mut inner, resource);
    inner.snapshot = HostSnapshot::ready(session);
    Ok(())
}

fn reserve_unpublished_launch_cleanup(shared: &SharedHostState, record: &OrphanRecord) -> bool {
    let (persisted, prior_health) = {
        let mut inner = shared.0.lock().expect("host state poisoned");
        let prior_health = inner.ownership_recovery;
        inner.configuration_epoch = inner.configuration_epoch.wrapping_add(1);
        if !inner.unpublished_orphans.contains(record) {
            if inner.unpublished_orphans.is_empty()
                && inner.ownership_recovery == OwnershipRecoveryState::Healthy
            {
                inner.snapshot_before_ownership_recovery = Some(inner.snapshot.clone());
            }
            inner.unpublished_orphans.push(record.clone());
        }
        let persisted = inner
            .repository
            .as_ref()
            .ok_or_else(|| std::io::Error::other("desktop state path unavailable"))
            .and_then(|repository| repository.add_unpublished_orphan(record))
            .is_ok();
        if persisted
            && !matches!(
                prior_health,
                OwnershipRecoveryState::Untrusted | OwnershipRecoveryState::PersistFailed
            )
        {
            inner.ownership_recovery = OwnershipRecoveryState::Recovering;
        }
        inner.snapshot = if matches!(
            prior_health,
            OwnershipRecoveryState::Untrusted | OwnershipRecoveryState::PersistFailed
        ) {
            HostSnapshot::persistence_failure()
        } else {
            HostSnapshot::unpublished_termination_failure()
        };
        (persisted, prior_health)
    };
    if persisted && prior_health != OwnershipRecoveryState::Untrusted {
        true
    } else {
        {
            let mut inner = shared.0.lock().expect("host state poisoned");
            inner.ownership_recovery = OwnershipRecoveryState::PersistFailed;
            inner.snapshot = HostSnapshot::persistence_failure();
        }
        let _ = append_diagnostic(
            shared,
            "state_persist_failed",
            "blocked",
            "desktop_state_persist_failed",
        );
        false
    }
}

fn finish_unpublished_launch_cleanup(
    shared: &SharedHostState,
    record: &OrphanRecord,
    result: std::io::Result<crate::lifecycle::TerminationOutcome>,
) -> bool {
    let safely_terminated = matches!(
        result,
        Ok(crate::lifecycle::TerminationOutcome::Stopped)
            | Ok(crate::lifecycle::TerminationOutcome::IdentityChanged)
    );
    if safely_terminated {
        let removed = {
            let mut inner = shared.0.lock().expect("host state poisoned");
            let removed = inner
                .repository
                .as_ref()
                .ok_or_else(|| std::io::Error::other("desktop state path unavailable"))
                .and_then(|repository| repository.remove_unpublished_orphan(record))
                .is_ok();
            if removed {
                inner
                    .unpublished_orphans
                    .retain(|candidate| candidate != record);
                if inner.unpublished_orphans.is_empty() {
                    inner.ownership_recovery = OwnershipRecoveryState::Healthy;
                    if let Some(snapshot) = inner.snapshot_before_ownership_recovery.take() {
                        inner.snapshot = snapshot;
                    }
                } else if !matches!(
                    inner.ownership_recovery,
                    OwnershipRecoveryState::Untrusted | OwnershipRecoveryState::PersistFailed
                ) {
                    inner.ownership_recovery = OwnershipRecoveryState::Recovering;
                }
            } else {
                inner.ownership_recovery = OwnershipRecoveryState::PersistFailed;
                inner.snapshot = HostSnapshot::persistence_failure();
            }
            removed
        };
        if removed {
            return true;
        }
        let _ = append_diagnostic(
            shared,
            "state_persist_failed",
            "blocked",
            "desktop_state_persist_failed",
        );
        return false;
    }

    {
        let mut inner = shared.0.lock().expect("host state poisoned");
        let unhealthy = matches!(
            inner.ownership_recovery,
            OwnershipRecoveryState::Untrusted | OwnershipRecoveryState::PersistFailed
        );
        if !unhealthy {
            inner.ownership_recovery = OwnershipRecoveryState::Recovering;
        }
        inner.snapshot = if unhealthy {
            HostSnapshot::persistence_failure()
        } else {
            HostSnapshot::unpublished_termination_failure()
        };
    }
    let _ = append_diagnostic(
        shared,
        "unpublished_sidecar_cleanup_failed",
        "blocked",
        "desktop_unpublished_sidecar_cleanup_failed",
    );
    false
}

async fn finalize_rejected_launch<T, F>(
    shared: &SharedHostState,
    reason: &'static str,
    record: OrphanRecord,
    child: T,
    termination: F,
) -> &'static str
where
    F: std::future::Future<Output = std::io::Result<crate::lifecycle::TerminationOutcome>>,
{
    let _child = child;
    if !reserve_unpublished_launch_cleanup(shared, &record) {
        return "desktop_unpublished_sidecar_cleanup_failed";
    }
    let result = termination.await;
    if finish_unpublished_launch_cleanup(shared, &record, result) {
        reason
    } else {
        "desktop_unpublished_sidecar_cleanup_failed"
    }
}

fn post_handshake_reject_reason(event: &CommandEvent) -> Option<&'static str> {
    match event {
        CommandEvent::Stdout(_) => Some("desktop_handshake_rejected"),
        _ => None,
    }
}

async fn verify_handshake_quiet(
    receiver: &mut tauri::async_runtime::Receiver<CommandEvent>,
) -> Result<(), &'static str> {
    let unexpected = async {
        loop {
            let Some(event) = receiver.recv().await else {
                return Err("desktop_sidecar_exited");
            };
            if let Some(reason) = post_handshake_reject_reason(&event) {
                return Err(reason);
            }
            if matches!(event, CommandEvent::Terminated(_) | CommandEvent::Error(_)) {
                return Err("desktop_sidecar_exited");
            }
        }
    };
    match tokio::time::timeout(HANDSHAKE_QUIET_PERIOD, unexpected).await {
        Ok(result) => result,
        Err(_) => Ok(()),
    }
}

async fn read_handshake(
    receiver: &mut tauri::async_runtime::Receiver<CommandEvent>,
) -> Result<Handshake, &'static str> {
    let future = async {
        while let Some(event) = receiver.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    return serde_json::from_slice::<Handshake>(&line)
                        .map_err(|_| "desktop_handshake_rejected");
                }
                CommandEvent::Terminated(_) | CommandEvent::Error(_) => {
                    return Err("desktop_sidecar_exited");
                }
                CommandEvent::Stderr(_) => {}
                _ => {}
            }
        }
        Err("desktop_sidecar_exited")
    };
    tokio::time::timeout(HANDSHAKE_TIMEOUT, future)
        .await
        .map_err(|_| "desktop_handshake_timeout")?
}

async fn monitor(
    app: AppHandle,
    shared: SharedHostState,
    data_dir: PathBuf,
    mut receiver: tauri::async_runtime::Receiver<CommandEvent>,
    generation: u64,
) {
    let mut reason = "desktop_sidecar_crashed";
    while let Some(event) = receiver.recv().await {
        if let Some(protocol_reason) = post_handshake_reject_reason(&event) {
            reason = protocol_reason;
            break;
        }
        if matches!(event, CommandEvent::Terminated(_) | CommandEvent::Error(_)) {
            break;
        }
    }
    if !shared.is_stopping() && is_current_generation(&shared, generation) {
        handle_crash(app, shared, data_dir, reason, generation);
    }
}

fn handle_crash(
    app: AppHandle,
    shared: SharedHostState,
    data_dir: PathBuf,
    reason: &'static str,
    generation: u64,
) {
    if !is_current_generation(&shared, generation) {
        return;
    }
    if lifecycle_action(LifecycleEvent::SidecarCrash) != LifecycleAction::RestartWithBackoff {
        return;
    }
    if let LaunchFailureDisposition::RetryAfter(seconds) =
        record_launch_failure(&shared, generation, reason, unix_seconds())
    {
        schedule_launch(
            app,
            shared,
            data_dir,
            Duration::from_secs(seconds),
            generation,
        );
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum LaunchFailureDisposition {
    Superseded,
    RecoverableConfiguration,
    RetryAfter(u64),
    Blocked,
}

impl LaunchFailureDisposition {
    #[cfg(test)]
    fn is_recoverable_configuration(self) -> bool {
        self == Self::RecoverableConfiguration
    }
}

fn recoverable_configuration_action(reason: &str) -> Option<&'static str> {
    match reason {
        "keychain_locked" => Some("unlock_keychain_and_retry"),
        "keychain_access_denied" => Some("allow_keychain_access_and_retry"),
        "keychain_entry_missing" => Some("reenter_provider_key"),
        "keychain_request_invalid" => Some("review_provider_settings"),
        "keychain_unavailable" => Some("retry_keychain_operation"),
        "desktop_metadata_unavailable" => Some("restart_sage"),
        "provider_reconciliation_required" => Some("retry_provider_reconciliation"),
        "desktop_unpublished_sidecar_cleanup_failed" => Some("open_diagnostics"),
        _ => None,
    }
}

fn record_launch_failure(
    shared: &SharedHostState,
    generation: u64,
    reason: &'static str,
    now: u64,
) -> LaunchFailureDisposition {
    record_launch_failure_with_hook(shared, generation, reason, now, || {})
}

fn record_launch_failure_with_hook<F>(
    shared: &SharedHostState,
    generation: u64,
    reason: &'static str,
    now: u64,
    after_admission_check: F,
) -> LaunchFailureDisposition
where
    F: FnOnce(),
{
    {
        let inner = shared.0.lock().expect("host state poisoned");
        if inner.launch_generation != generation {
            return LaunchFailureDisposition::Superseded;
        }
        if ownership_admission_failure(&inner).is_some() {
            return LaunchFailureDisposition::Blocked;
        }
    }
    after_admission_check();
    if reason == "desktop_launch_superseded" {
        return LaunchFailureDisposition::Superseded;
    }
    if let Some(action) = recoverable_configuration_action(reason) {
        let persistence_failed = {
            let mut inner = shared.0.lock().expect("host state poisoned");
            if inner.launch_generation != generation {
                return LaunchFailureDisposition::Superseded;
            }
            if ownership_admission_failure(&inner).is_some() {
                return LaunchFailureDisposition::Blocked;
            }
            inner.pid = None;
            inner.child = None;
            inner.snapshot = HostSnapshot::problem("blocked", reason, action);
            persist_disk_locked(&inner).is_err()
        };
        if persistence_failed {
            set_snapshot(shared, HostSnapshot::persistence_failure());
            let _ = append_diagnostic(
                shared,
                "state_persist_failed",
                "blocked",
                "desktop_state_persist_failed",
            );
            return LaunchFailureDisposition::Blocked;
        }
        let _ = append_diagnostic(shared, "configuration_blocked", "blocked", reason);
        return LaunchFailureDisposition::RecoverableConfiguration;
    }
    let (delay, persistence_failed) = {
        let mut inner = shared.0.lock().expect("host state poisoned");
        if inner.launch_generation != generation {
            return LaunchFailureDisposition::Superseded;
        }
        if ownership_admission_failure(&inner).is_some() {
            return LaunchFailureDisposition::Blocked;
        }
        inner.pid = None;
        inner.child = None;
        inner.disk.orphan = None;
        let result = inner.disk.crash_budget.record_crash(now);
        let persistence_failed = persist_disk_locked(&inner).is_err();
        if persistence_failed {
            inner.snapshot = HostSnapshot::persistence_failure();
            (None, true)
        } else if result.is_some() {
            inner.snapshot = HostSnapshot::problem("degraded", reason, "wait_for_restart");
            (result, false)
        } else {
            inner.snapshot = HostSnapshot::problem(
                "blocked",
                "desktop_crash_budget_exhausted",
                "open_diagnostics",
            );
            (result, false)
        }
    };
    let _ = append_diagnostic(
        shared,
        if persistence_failed {
            "state_persist_failed"
        } else {
            "sidecar_crash"
        },
        if persistence_failed || delay.is_none() {
            "blocked"
        } else {
            "degraded"
        },
        if persistence_failed {
            "desktop_state_persist_failed"
        } else if delay.is_none() {
            "desktop_crash_budget_exhausted"
        } else {
            reason
        },
    );
    match (delay, persistence_failed) {
        (_, true) | (None, false) => LaunchFailureDisposition::Blocked,
        (Some(seconds), false) => LaunchFailureDisposition::RetryAfter(seconds),
    }
}

fn ownership_admission_failure(inner: &HostInner) -> Option<&'static str> {
    match inner.ownership_recovery {
        OwnershipRecoveryState::Healthy if inner.unpublished_orphans.is_empty() => None,
        OwnershipRecoveryState::Recovering | OwnershipRecoveryState::Healthy => {
            Some("desktop_unpublished_sidecar_cleanup_failed")
        }
        OwnershipRecoveryState::Untrusted | OwnershipRecoveryState::PersistFailed => {
            Some("desktop_state_persist_failed")
        }
    }
}

fn launch_admission_failure(shared: &SharedHostState, generation: u64) -> Option<&'static str> {
    let inner = shared.0.lock().expect("host state poisoned");
    if inner.stopping {
        return Some("desktop_stopping");
    }
    if inner.launch_generation != generation || inner.configuration_restart_in_progress {
        return Some("desktop_launch_superseded");
    }
    ownership_admission_failure(&inner)
}

fn next_launch_generation(shared: &SharedHostState) -> Option<u64> {
    let mut inner = shared.0.lock().expect("host state poisoned");
    if inner.stopping || ownership_admission_failure(&inner).is_some() {
        return None;
    }
    inner.launch_generation = inner.launch_generation.wrapping_add(1);
    Some(inner.launch_generation)
}

fn is_current_generation(shared: &SharedHostState, generation: u64) -> bool {
    shared
        .0
        .lock()
        .expect("host state poisoned")
        .launch_generation
        == generation
}

pub(crate) fn restart_for_configuration(
    app: AppHandle,
    shared: SharedHostState,
    receipt: ConfigurationRestartReceipt,
) -> Result<(), ConfigurationActionFailure> {
    let data_dir = app.path().app_data_dir().ok();
    let data_dir_available = data_dir.is_some();
    if !data_dir_available {
        set_problem(
            &shared,
            "blocked",
            "desktop_data_dir_unavailable",
            "restart_sage",
        );
    }
    let request = receipt.request;
    tauri::async_runtime::spawn(async move {
        let stopped = stop_sidecar_for_configuration(&shared, &request).await;
        if !stopped {
            mark_configuration_stop_failed(&shared, &request);
        }
        let completed = finish_configuration_restart(&shared, request.generation);
        if let Some(data_dir) = data_dir.filter(|_| {
            stopped
                && completed
                && !shared.is_stopping()
                && is_current_generation(&shared, request.generation)
        }) {
            schedule_launch(app, shared, data_dir, Duration::ZERO, request.generation);
        }
    });
    if !data_dir_available {
        Err(configuration_failure(
            "desktop_data_dir_unavailable",
            "restart_sage",
        ))
    } else {
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ConfigurationRestartRequest {
    generation: u64,
    orphan: Option<OrphanRecord>,
}

#[cfg(test)]
fn begin_configuration_restart(shared: &SharedHostState) -> Option<ConfigurationRestartRequest> {
    let mut inner = shared.0.lock().expect("host state poisoned");
    if inner.configuration_restart_in_progress || ownership_admission_failure(&inner).is_some() {
        return None;
    }
    Some(begin_configuration_restart_locked(&mut inner))
}

fn begin_configuration_restart_locked(inner: &mut HostInner) -> ConfigurationRestartRequest {
    inner.snapshot = HostSnapshot::starting();
    inner.launch_generation = inner.launch_generation.wrapping_add(1);
    inner.configuration_restart_in_progress = true;
    inner.configuration_epoch = inner.configuration_epoch.wrapping_add(1);
    ConfigurationRestartRequest {
        generation: inner.launch_generation,
        orphan: inner.disk.orphan.clone(),
    }
}

async fn stop_sidecar_for_configuration(
    shared: &SharedHostState,
    request: &ConfigurationRestartRequest,
) -> bool {
    let owned = shared.clone();
    let request = request.clone();
    tauri::async_runtime::spawn_blocking(move || {
        stop_sidecar_blocking_for_configuration(&owned, &request)
    })
    .await
    .unwrap_or(false)
}

fn stop_sidecar_blocking_for_configuration(
    shared: &SharedHostState,
    request: &ConfigurationRestartRequest,
) -> bool {
    let child = {
        let mut inner = shared.0.lock().expect("host state poisoned");
        if inner.launch_generation != request.generation
            || !inner.configuration_restart_in_progress
            || inner.disk.orphan != request.orphan
        {
            return false;
        }
        inner.child.take()
    };
    if let Some(record) = request.orphan.as_ref() {
        let _child = child;
        finish_runtime_termination_for_generation(
            shared,
            record,
            request.generation,
            terminate_runtime(record),
        )
    } else {
        let stopped = child.is_none();
        drop(child);
        if stopped {
            let mut inner = shared.0.lock().expect("host state poisoned");
            if inner.launch_generation != request.generation
                || !inner.configuration_restart_in_progress
                || inner.disk.orphan.is_some()
            {
                return false;
            }
            inner.pid = None;
        }
        stopped
    }
}

fn finish_configuration_restart(shared: &SharedHostState, generation: u64) -> bool {
    let mut inner = shared.0.lock().expect("host state poisoned");
    if inner.launch_generation != generation || !inner.configuration_restart_in_progress {
        return false;
    }
    inner.configuration_restart_in_progress = false;
    true
}

fn mark_configuration_stop_failed(shared: &SharedHostState, request: &ConfigurationRestartRequest) {
    {
        let mut inner = shared.0.lock().expect("host state poisoned");
        if inner.launch_generation != request.generation
            || !inner.configuration_restart_in_progress
            || inner.disk.orphan != request.orphan
        {
            return;
        }
        inner.snapshot = HostSnapshot::termination_failure();
    }
    let _ = append_diagnostic(
        shared,
        "sidecar_stop_failed",
        "blocked",
        "desktop_sidecar_stop_failed",
    );
}

async fn stop_sidecar(shared: &SharedHostState) -> bool {
    let owned = shared.clone();
    tauri::async_runtime::spawn_blocking(move || stop_sidecar_blocking(&owned))
        .await
        .unwrap_or(false)
}

fn stop_sidecar_blocking(shared: &SharedHostState) -> bool {
    let (record, child) = {
        let mut inner = shared.0.lock().expect("host state poisoned");
        (inner.disk.orphan.clone(), inner.child.take())
    };
    if let Some(record) = record {
        let _child = child;
        let result = terminate_runtime(&record);
        finish_runtime_termination(shared, &record, result)
    } else {
        let stopped = child.is_none();
        drop(child);
        shared.0.lock().expect("host state poisoned").pid = None;
        stopped
    }
}

fn finish_runtime_termination(
    shared: &SharedHostState,
    record: &OrphanRecord,
    result: std::io::Result<crate::lifecycle::TerminationOutcome>,
) -> bool {
    finish_runtime_termination_guarded(shared, record, None, result)
}

fn finish_runtime_termination_for_generation(
    shared: &SharedHostState,
    record: &OrphanRecord,
    generation: u64,
    result: std::io::Result<crate::lifecycle::TerminationOutcome>,
) -> bool {
    finish_runtime_termination_guarded(shared, record, Some(generation), result)
}

fn finish_runtime_termination_guarded(
    shared: &SharedHostState,
    record: &OrphanRecord,
    generation: Option<u64>,
    result: std::io::Result<crate::lifecycle::TerminationOutcome>,
) -> bool {
    let (stopped, persistence_failed) = {
        let mut inner = shared.0.lock().expect("host state poisoned");
        if generation.is_some_and(|expected| inner.launch_generation != expected)
            || inner.disk.orphan.as_ref() != Some(record)
        {
            return false;
        }
        let stopped = matches!(result, Ok(crate::lifecycle::TerminationOutcome::Stopped));
        if stopped {
            inner.pid = None;
            inner.disk.orphan = None;
        } else {
            inner.snapshot = HostSnapshot::termination_failure();
        }
        let persistence_failed = persist_disk_locked(&inner).is_err();
        if persistence_failed {
            if stopped {
                inner.disk.orphan = Some(record.clone());
            }
            inner.snapshot = HostSnapshot::persistence_failure();
        }
        (stopped, persistence_failed)
    };
    if persistence_failed {
        let _ = append_diagnostic(
            shared,
            "state_persist_failed",
            "blocked",
            "desktop_state_persist_failed",
        );
    } else if !stopped {
        let _ = append_diagnostic(
            shared,
            "sidecar_stop_failed",
            "blocked",
            "desktop_sidecar_stop_failed",
        );
    }
    stopped && !persistence_failed
}

fn probe_health(port: u16, bearer: &str, build_sha: &str, origin: &str) -> Result<(), ()> {
    let deadline = std::time::Instant::now() + HEALTH_TIMEOUT;
    loop {
        let live = http_json(port, "/health/live", bearer, origin);
        let ready = http_json(port, "/health/ready", bearer, origin);
        if live.as_ref().is_ok_and(|payload| {
            payload.get("status").and_then(Value::as_str) == Some("live")
                && payload.get("api_version").and_then(Value::as_str) == Some(API_VERSION)
                && payload.get("build_sha").and_then(Value::as_str) == Some(build_sha)
        }) && ready
            .as_ref()
            .is_ok_and(|payload| validate_ready(payload, build_sha))
        {
            return Ok(());
        }
        if std::time::Instant::now() >= deadline {
            return Err(());
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn validate_ready(payload: &Value, build_sha: &str) -> bool {
    if payload.get("status").and_then(Value::as_str) != Some("ready")
        || payload.get("api_version").and_then(Value::as_str) != Some(API_VERSION)
        || payload.get("build_sha").and_then(Value::as_str) != Some(build_sha)
    {
        return false;
    }
    let Some(checks) = payload.get("checks").and_then(Value::as_object) else {
        return false;
    };
    [
        "api",
        "build",
        "schema",
        "storage",
        "checkpoint",
        "tls",
        "core_imports",
    ]
    .iter()
    .all(|name| {
        checks
            .get(*name)
            .and_then(|value| value.get("status"))
            .and_then(Value::as_str)
            == Some("ready")
    })
}

fn http_json(port: u16, path: &str, bearer: &str, origin: &str) -> Result<Value, ()> {
    let address = ("127.0.0.1", port)
        .to_socket_addrs()
        .map_err(|_| ())?
        .next()
        .ok_or(())?;
    let mut stream =
        TcpStream::connect_timeout(&address, Duration::from_millis(500)).map_err(|_| ())?;
    stream
        .set_read_timeout(Some(Duration::from_secs(1)))
        .map_err(|_| ())?;
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: {origin}\r\nAuthorization: Bearer {bearer}\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).map_err(|_| ())?;
    let mut response = Vec::new();
    stream.read_to_end(&mut response).map_err(|_| ())?;
    let boundary = response
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .ok_or(())?;
    let head = std::str::from_utf8(&response[..boundary]).map_err(|_| ())?;
    if !head.starts_with("HTTP/1.1 200") {
        return Err(());
    }
    serde_json::from_slice(&response[boundary + 4..]).map_err(|_| ())
}

fn random_secret() -> String {
    let mut bytes = [0_u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    URL_SAFE_NO_PAD.encode(bytes)
}

fn set_snapshot(shared: &SharedHostState, snapshot: HostSnapshot) {
    shared.0.lock().expect("host state poisoned").snapshot = snapshot;
}

fn set_problem(
    shared: &SharedHostState,
    state: &'static str,
    reason: &'static str,
    action: &'static str,
) {
    set_snapshot(shared, HostSnapshot::problem(state, reason, action));
}

fn unix_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

fn runtime_origin() -> &'static str {
    origin_for_profile(cfg!(debug_assertions))
}

fn origin_for_profile(debug: bool) -> &'static str {
    if debug {
        DEVELOPMENT_ORIGIN
    } else {
        PRODUCTION_ORIGIN
    }
}

fn observe_process(pid: u32) -> Option<ObservedProcess> {
    let mut system = System::new();
    let sys_pid = Pid::from_u32(pid);
    system.refresh_processes(ProcessesToUpdate::Some(&[sys_pid]), true);
    let process = system.process(sys_pid)?;
    Some(ObservedProcess {
        pid,
        start_time: process.start_time(),
        executable: process.exe()?.to_path_buf(),
    })
}

fn clean_known_orphan(disk: &mut HostDiskState) -> std::io::Result<()> {
    let Some(record) = disk.orphan.as_ref() else {
        return Ok(());
    };
    let outcome = terminate_runtime(record)?;
    apply_startup_termination_outcome(disk, outcome)
}

struct UnpublishedStartupReconciliation {
    records: Vec<OrphanRecord>,
    health: OwnershipRecoveryState,
    cleanup_failed: bool,
}

fn reconcile_unpublished_startup<F>(
    repository: &DesktopStateRepository,
    terminate: F,
) -> UnpublishedStartupReconciliation
where
    F: FnMut(&OrphanRecord) -> std::io::Result<crate::lifecycle::TerminationOutcome>,
{
    reconcile_unpublished_startup_with_replace(repository, terminate, |repository, remaining| {
        repository.replace_unpublished_orphans_verified(remaining)
    })
}

fn reconcile_unpublished_startup_with_replace<F, R>(
    repository: &DesktopStateRepository,
    mut terminate: F,
    replace_and_reload: R,
) -> UnpublishedStartupReconciliation
where
    F: FnMut(&OrphanRecord) -> std::io::Result<crate::lifecycle::TerminationOutcome>,
    R: FnOnce(&DesktopStateRepository, &[OrphanRecord]) -> std::io::Result<Vec<OrphanRecord>>,
{
    let original = match repository.load_unpublished_orphans() {
        Ok(records) => records,
        Err(_) => {
            return UnpublishedStartupReconciliation {
                records: Vec::new(),
                health: OwnershipRecoveryState::Untrusted,
                cleanup_failed: false,
            }
        }
    };
    let mut remaining = original.clone();
    let mut cleanup_failed = false;
    for record in &original {
        match terminate(record) {
            Ok(crate::lifecycle::TerminationOutcome::Stopped)
            | Ok(crate::lifecycle::TerminationOutcome::IdentityChanged) => {
                remaining.retain(|candidate| candidate != record);
            }
            Ok(crate::lifecycle::TerminationOutcome::KillSent) | Err(_) => {
                cleanup_failed = true;
            }
        }
    }
    let verified_remaining = match replace_and_reload(repository, &remaining) {
        Ok(records) => records,
        Err(_) => {
            return UnpublishedStartupReconciliation {
                records: original,
                health: OwnershipRecoveryState::PersistFailed,
                cleanup_failed,
            };
        }
    };
    UnpublishedStartupReconciliation {
        health: if verified_remaining.is_empty() {
            OwnershipRecoveryState::Healthy
        } else {
            OwnershipRecoveryState::Recovering
        },
        records: verified_remaining,
        cleanup_failed,
    }
}

fn apply_startup_termination_outcome(
    disk: &mut HostDiskState,
    outcome: crate::lifecycle::TerminationOutcome,
) -> std::io::Result<()> {
    match outcome {
        crate::lifecycle::TerminationOutcome::Stopped => {
            disk.orphan = None;
            Ok(())
        }
        crate::lifecycle::TerminationOutcome::IdentityChanged
        | crate::lifecycle::TerminationOutcome::KillSent => Err(std::io::Error::other(
            "desktop sidecar stop was not verified",
        )),
    }
}

fn persist_disk_locked(inner: &HostInner) -> std::io::Result<()> {
    let Some(repository) = inner.repository.as_ref() else {
        return Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "desktop state path unavailable",
        ));
    };
    repository.save(&inner.disk)
}

fn terminate_runtime(
    record: &OrphanRecord,
) -> std::io::Result<crate::lifecycle::TerminationOutcome> {
    let pid = record.pid;
    let grace_checks = (EXIT_GRACE.as_millis() / 50) as usize;
    terminate_verified(
        record,
        || observe_process(pid),
        |signal| {
            let raw_signal = match signal {
                ProcessSignal::Term => libc::SIGTERM,
                ProcessSignal::Kill => libc::SIGKILL,
            };
            let result = unsafe { libc::kill(pid as i32, raw_signal) };
            if result == 0 {
                Ok(())
            } else {
                let error = std::io::Error::last_os_error();
                if error.raw_os_error() == Some(libc::ESRCH) {
                    Ok(())
                } else {
                    Err(error)
                }
            }
        },
        || std::thread::sleep(Duration::from_millis(50)),
        grace_checks,
    )
}

async fn terminate_runtime_async(
    record: OrphanRecord,
) -> std::io::Result<crate::lifecycle::TerminationOutcome> {
    tauri::async_runtime::spawn_blocking(move || terminate_runtime(&record))
        .await
        .map_err(|_| std::io::Error::other("desktop sidecar cleanup task failed"))?
}

fn append_diagnostic(
    shared: &SharedHostState,
    event: &str,
    state: &str,
    reason_code: &str,
) -> std::io::Result<()> {
    let log = shared
        .0
        .lock()
        .expect("host state poisoned")
        .diagnostic_log
        .clone();
    let Some(log) = log else {
        return Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "desktop diagnostics path unavailable",
        ));
    };
    log.append(event, state, reason_code)
}

#[cfg(test)]
mod tests {
    use super::{
        acquire_configuration_mutation_guard, append_diagnostic, apply_startup_termination_outcome,
        begin_configuration_restart, commit_launch_success, finalize_rejected_launch,
        finish_configuration_restart, finish_runtime_termination,
        finish_runtime_termination_for_generation, finish_unpublished_launch_cleanup,
        mark_configuration_stop_failed, origin_for_profile, post_handshake_reject_reason,
        reconcile_unpublished_startup, reconcile_unpublished_startup_with_replace,
        record_launch_failure, record_launch_failure_with_hook, reserve_unpublished_launch_cleanup,
        runtime_origin, transition_launch_to_starting, ConfigurationMutationCommitError,
        ConfigurationRestartRequest, DesktopStateRepository, DiagnosticLog, HostSnapshot,
        OwnershipRecoveryState, SharedHostState, DEVELOPMENT_ORIGIN, PRODUCTION_ORIGIN,
    };
    use crate::lifecycle::{OrphanRecord, TerminationOutcome};
    use crate::protocol::DesktopSession;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Arc, Barrier};
    use tauri_plugin_shell::process::CommandEvent;

    #[test]
    fn duplicate_stdout_after_handshake_is_rejected() {
        let event = CommandEvent::Stdout(br#"{"pid":42}"#.to_vec());

        assert_eq!(
            post_handshake_reject_reason(&event),
            Some("desktop_handshake_rejected")
        );
        assert_eq!(
            post_handshake_reject_reason(&CommandEvent::Stderr(b"diagnostic".to_vec())),
            None
        );
    }

    #[test]
    fn debug_host_and_sidecar_share_the_exact_vite_origin() {
        assert_eq!(runtime_origin(), DEVELOPMENT_ORIGIN);
        assert_eq!(origin_for_profile(true), DEVELOPMENT_ORIGIN);
        assert_eq!(origin_for_profile(false), PRODUCTION_ORIGIN);
    }

    #[test]
    fn persistence_failure_is_never_published_as_ready() {
        let snapshot = HostSnapshot::persistence_failure();

        assert_eq!(snapshot.state, "blocked");
        assert_eq!(snapshot.reason_code, Some("desktop_state_persist_failed"));
        assert_eq!(snapshot.action, Some("open_diagnostics"));
        assert!(snapshot.session.is_none());
    }

    #[test]
    fn termination_failure_preserves_orphan_and_publishes_blocked_diagnostic() {
        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        let record = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(DesktopStateRepository::new(
                root.path().join("desktop-host-state.json"),
            ));
            inner.diagnostic_log =
                Some(DiagnosticLog::create(root.path().join("diagnostics")).unwrap());
            inner.disk.orphan = Some(record.clone());
        }

        assert!(!finish_runtime_termination(
            &shared,
            &record,
            Err(std::io::Error::from(std::io::ErrorKind::PermissionDenied)),
        ));

        let inner = shared.0.lock().unwrap();
        assert_eq!(inner.disk.orphan.as_ref(), Some(&record));
        assert_eq!(inner.snapshot.state, "blocked");
        assert_eq!(
            inner.snapshot.reason_code,
            Some("desktop_sidecar_stop_failed")
        );
        drop(inner);
        let persisted: serde_json::Value = serde_json::from_slice(
            &std::fs::read(root.path().join("desktop-host-state.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(
            persisted.pointer("/orphan/pid"),
            Some(&serde_json::json!(42))
        );
        let diagnostic =
            std::fs::read_to_string(root.path().join("diagnostics/desktop-host.jsonl")).unwrap();
        assert!(diagnostic.contains("desktop_sidecar_stop_failed"));
    }

    #[test]
    fn recoverable_pre_spawn_configuration_errors_do_not_consume_crash_budget() {
        for reason in [
            "keychain_locked",
            "keychain_access_denied",
            "keychain_entry_missing",
        ] {
            let root = tempfile::tempdir().unwrap();
            let shared = SharedHostState::default();
            {
                let mut inner = shared.0.lock().unwrap();
                inner.repository =
                    Some(DesktopStateRepository::new(root.path().join("state.json")));
                inner.diagnostic_log =
                    Some(DiagnosticLog::create(root.path().join("diagnostics")).unwrap());
            }

            let retry = record_launch_failure(&shared, 0, reason, 100);

            assert!(retry.is_recoverable_configuration());
            let persisted: serde_json::Value =
                serde_json::from_slice(&std::fs::read(root.path().join("state.json")).unwrap())
                    .unwrap();
            assert_eq!(
                persisted["crash_budget"]["crash_timestamps"],
                serde_json::json!([])
            );
            let inner = shared.0.lock().unwrap();
            assert_eq!(inner.snapshot.state, "blocked");
            assert_eq!(inner.snapshot.reason_code, Some(reason));
        }
    }

    #[test]
    fn superseded_launch_failure_does_not_mutate_new_generation_ownership_or_budget() {
        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        let record = OrphanRecord {
            pid: 99,
            start_time: 200,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(DesktopStateRepository::new(root.path().join("state.json")));
            inner.launch_generation = 2;
            inner.pid = Some(record.pid);
            inner.disk.orphan = Some(record.clone());
            inner.snapshot = HostSnapshot::problem("ready", "new_generation_ready", "none");
        }

        let _disposition = record_launch_failure(&shared, 1, "desktop_launch_superseded", 100);

        let inner = shared.0.lock().unwrap();
        assert_eq!(inner.pid, Some(record.pid));
        assert_eq!(inner.disk.orphan.as_ref(), Some(&record));
        assert_eq!(
            serde_json::to_value(&inner.disk.crash_budget).unwrap(),
            serde_json::json!({ "crash_timestamps": [] })
        );
        assert_eq!(inner.snapshot.reason_code, Some("new_generation_ready"));
    }

    #[test]
    fn old_generation_failure_after_new_ready_is_cancelled_before_accounting() {
        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        let record = OrphanRecord {
            pid: 99,
            start_time: 200,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(DesktopStateRepository::new(root.path().join("state.json")));
            inner.launch_generation = 2;
            inner.pid = Some(record.pid);
            inner.disk.orphan = Some(record.clone());
            inner.snapshot = HostSnapshot::problem("ready", "new_generation_ready", "none");
        }

        let disposition = record_launch_failure(&shared, 1, "desktop_sidecar_spawn_failed", 100);

        assert_eq!(disposition, super::LaunchFailureDisposition::Superseded);
        let inner = shared.0.lock().unwrap();
        assert_eq!(inner.pid, Some(record.pid));
        assert_eq!(inner.disk.orphan.as_ref(), Some(&record));
        assert_eq!(inner.snapshot.reason_code, Some("new_generation_ready"));
    }

    #[test]
    fn launch_success_commit_rejects_restart_inserted_after_precheck_and_returns_child() {
        struct PendingTestChild;

        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(DesktopStateRepository::new(root.path().join("state.json")));
            inner.launch_generation = 1;
        }
        let old_generation = 1;
        assert!(super::is_current_generation(&shared, old_generation));

        let restart = begin_configuration_restart(&shared).unwrap();
        let record = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        let published = Arc::new(AtomicBool::new(false));
        let publish_observer = published.clone();
        let result = commit_launch_success(
            &shared,
            old_generation,
            record.clone(),
            DesktopSession {
                endpoint: "http://127.0.0.1:4242".into(),
                bearer: "test-bearer".into(),
                instance_id: "old-instance".into(),
            },
            PendingTestChild,
            move |_, _| publish_observer.store(true, Ordering::SeqCst),
        );

        let (reason, rejected_record, _unpublished_child) =
            result.expect_err("old launch must be cancelled");
        assert_eq!(reason, "desktop_launch_superseded");
        assert_eq!(rejected_record, record);
        assert!(!published.load(Ordering::SeqCst));
        {
            let inner = shared.0.lock().unwrap();
            assert_eq!(inner.launch_generation, restart.generation);
            assert!(inner.disk.orphan.is_none());
            assert!(inner.pid.is_none());
            assert!(inner.child.is_none());
            assert_eq!(inner.snapshot.state, "starting");
            assert!(inner.snapshot.session.is_none());
        }
        assert!(finish_configuration_restart(&shared, restart.generation));
    }

    #[test]
    fn unpublished_cleanup_failure_persists_identity_and_blocks() {
        let cases = [
            Err(std::io::Error::from(std::io::ErrorKind::PermissionDenied)),
            Err(std::io::Error::from(std::io::ErrorKind::TimedOut)),
            Ok(TerminationOutcome::KillSent),
        ];
        for result in cases {
            let root = tempfile::tempdir().unwrap();
            let shared = SharedHostState::default();
            let repository = DesktopStateRepository::new(root.path().join("state.json"));
            let unpublished_path = repository.unpublished_path().to_path_buf();
            let record = OrphanRecord {
                pid: 42,
                start_time: 100,
                executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
            };
            {
                let mut inner = shared.0.lock().unwrap();
                inner.repository = Some(repository);
                inner.diagnostic_log =
                    Some(DiagnosticLog::create(root.path().join("diagnostics")).unwrap());
                super::persist_disk_locked(&inner).unwrap();
            }

            assert!(reserve_unpublished_launch_cleanup(&shared, &record));
            assert!(!finish_unpublished_launch_cleanup(&shared, &record, result));
            let persisted: serde_json::Value =
                serde_json::from_slice(&std::fs::read(unpublished_path).unwrap()).unwrap();
            assert_eq!(
                persisted.pointer("/unpublished_orphans/0/pid"),
                Some(&serde_json::json!(42))
            );
            let inner = shared.0.lock().unwrap();
            assert_eq!(inner.snapshot.state, "blocked");
            assert_eq!(
                inner.snapshot.reason_code,
                Some("desktop_unpublished_sidecar_cleanup_failed")
            );
            assert_eq!(inner.snapshot.action, Some("open_diagnostics"));
            assert_eq!(
                inner.unpublished_orphans.as_slice(),
                std::slice::from_ref(&record)
            );
            drop(inner);
            let diagnostic =
                std::fs::read_to_string(root.path().join("diagnostics/desktop-host.jsonl"))
                    .unwrap();
            assert!(diagnostic.contains("desktop_unpublished_sidecar_cleanup_failed"));
        }
    }

    #[test]
    fn rejected_launch_caller_keeps_child_until_verified_cleanup_and_blocks_on_failure() {
        struct PendingTestChild(Arc<AtomicBool>);

        impl Drop for PendingTestChild {
            fn drop(&mut self) {
                self.0.store(false, Ordering::SeqCst);
            }
        }

        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        let repository = DesktopStateRepository::new(root.path().join("state.json"));
        let record = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(repository.clone());
            inner.diagnostic_log =
                Some(DiagnosticLog::create(root.path().join("diagnostics")).unwrap());
        }
        let child_alive = Arc::new(AtomicBool::new(true));
        let observed_alive = child_alive.clone();

        let reason = tauri::async_runtime::block_on(finalize_rejected_launch(
            &shared,
            "desktop_launch_superseded",
            record.clone(),
            PendingTestChild(child_alive.clone()),
            async move {
                assert!(observed_alive.load(Ordering::SeqCst));
                Err(std::io::Error::from(std::io::ErrorKind::PermissionDenied))
            },
        ));

        assert_eq!(reason, "desktop_unpublished_sidecar_cleanup_failed");
        assert!(!child_alive.load(Ordering::SeqCst));
        assert_eq!(repository.load_unpublished_orphans().unwrap(), [record]);
        let snapshot = &shared.0.lock().unwrap().snapshot;
        assert_eq!(snapshot.state, "blocked");
        assert_eq!(
            snapshot.reason_code,
            Some("desktop_unpublished_sidecar_cleanup_failed")
        );
    }

    #[test]
    fn rejected_launch_is_durably_reserved_before_cleanup_future_and_closes_admission() {
        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        let repository = DesktopStateRepository::new(root.path().join("state.json"));
        let record = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(repository.clone());
            inner.diagnostic_log =
                Some(DiagnosticLog::create(root.path().join("diagnostics")).unwrap());
            inner.launch_generation = 1;
        }
        let cleanup_started = Arc::new(Barrier::new(2));
        let cleanup_resume = Arc::new(Barrier::new(2));
        let worker_shared = shared.clone();
        let worker_record = record.clone();
        let worker_started = cleanup_started.clone();
        let worker_resume = cleanup_resume.clone();
        let worker = std::thread::spawn(move || {
            tauri::async_runtime::block_on(finalize_rejected_launch(
                &worker_shared,
                "desktop_launch_superseded",
                worker_record,
                (),
                async move {
                    worker_started.wait();
                    worker_resume.wait();
                    Ok(TerminationOutcome::Stopped)
                },
            ))
        });

        cleanup_started.wait();
        let persisted_during_cleanup = repository.load_unpublished_orphans().unwrap();
        let restarted_repository = DesktopStateRepository::new(root.path().join("state.json"));
        let restarted_recovery = reconcile_unpublished_startup(&restarted_repository, |_| {
            Ok(TerminationOutcome::KillSent)
        });
        let restart_during_cleanup = begin_configuration_restart(&shared);
        let generation_during_cleanup = super::next_launch_generation(&shared);
        let starting_during_cleanup = transition_launch_to_starting(&shared, 1, || {});
        let commit_during_cleanup = commit_launch_success(
            &shared,
            1,
            OrphanRecord {
                pid: 99,
                start_time: 200,
                executable: record.executable.clone(),
            },
            DesktopSession {
                endpoint: "http://127.0.0.1:4999".into(),
                bearer: "new-bearer".into(),
                instance_id: "new-instance".into(),
            },
            (),
            |_, _| {},
        );
        cleanup_resume.wait();
        let _ = worker.join().unwrap();

        assert_eq!(persisted_during_cleanup, std::slice::from_ref(&record));
        assert_eq!(restarted_recovery.records, std::slice::from_ref(&record));
        assert_eq!(
            restarted_recovery.health,
            OwnershipRecoveryState::Recovering
        );
        assert!(restart_during_cleanup.is_none());
        assert_eq!(generation_during_cleanup, None);
        assert_eq!(
            starting_during_cleanup,
            Err("desktop_unpublished_sidecar_cleanup_failed")
        );
        assert!(matches!(
            commit_during_cleanup,
            Err(("desktop_unpublished_sidecar_cleanup_failed", _, _))
        ));
    }

    #[test]
    fn failed_write_ahead_append_does_not_poll_cleanup_and_remains_fail_closed() {
        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        let repository = DesktopStateRepository::new(root.path().join("state.json"));
        std::fs::create_dir(repository.unpublished_path()).unwrap();
        let record = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(repository);
            inner.diagnostic_log =
                Some(DiagnosticLog::create(root.path().join("diagnostics")).unwrap());
            inner.launch_generation = 1;
        }
        let cleanup_polled = Arc::new(AtomicBool::new(false));
        let observed_poll = cleanup_polled.clone();

        let reason = tauri::async_runtime::block_on(finalize_rejected_launch(
            &shared,
            "desktop_launch_superseded",
            record.clone(),
            (),
            async move {
                observed_poll.store(true, Ordering::SeqCst);
                Ok(TerminationOutcome::Stopped)
            },
        ));

        assert_eq!(reason, "desktop_unpublished_sidecar_cleanup_failed");
        assert!(!cleanup_polled.load(Ordering::SeqCst));
        let inner = shared.0.lock().unwrap();
        assert_eq!(inner.unpublished_orphans, [record]);
        assert_eq!(
            inner.ownership_recovery,
            OwnershipRecoveryState::PersistFailed
        );
        assert_eq!(
            inner.snapshot.reason_code,
            Some("desktop_state_persist_failed")
        );
        drop(inner);
        assert!(begin_configuration_restart(&shared).is_none());
        assert_eq!(super::next_launch_generation(&shared), None);
    }

    #[test]
    fn later_reservation_cannot_downgrade_persist_failed_journal_health() {
        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        let repository = DesktopStateRepository::new(root.path().join("state.json"));
        let lost = OrphanRecord {
            pid: 41,
            start_time: 99,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        let later = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: lost.executable.clone(),
        };
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(repository);
            inner.unpublished_orphans.push(lost.clone());
            inner.ownership_recovery = OwnershipRecoveryState::PersistFailed;
            inner.snapshot = HostSnapshot::persistence_failure();
        }

        assert!(reserve_unpublished_launch_cleanup(&shared, &later));
        assert!(!finish_unpublished_launch_cleanup(
            &shared,
            &later,
            Ok(TerminationOutcome::KillSent),
        ));

        let inner = shared.0.lock().unwrap();
        assert_eq!(inner.unpublished_orphans, [lost, later]);
        assert_eq!(
            inner.ownership_recovery,
            OwnershipRecoveryState::PersistFailed
        );
        assert_eq!(
            inner.snapshot.reason_code,
            Some("desktop_state_persist_failed")
        );
    }

    #[test]
    fn pending_unpublished_ownership_is_not_overwritten_by_failure_accounting() {
        let shared = SharedHostState::default();
        {
            let mut inner = shared.0.lock().unwrap();
            inner.launch_generation = 7;
            inner.unpublished_orphans.push(OrphanRecord {
                pid: 42,
                start_time: 100,
                executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
            });
            inner.snapshot = HostSnapshot::persistence_failure();
        }

        assert_eq!(
            record_launch_failure(
                &shared,
                7,
                "desktop_unpublished_sidecar_cleanup_failed",
                100,
            ),
            super::LaunchFailureDisposition::Blocked
        );
        let inner = shared.0.lock().unwrap();
        assert_eq!(inner.snapshot.state, "blocked");
        assert_eq!(
            inner.snapshot.reason_code,
            Some("desktop_state_persist_failed")
        );
        assert_eq!(inner.snapshot.action, Some("open_diagnostics"));
        assert_eq!(inner.unpublished_orphans.len(), 1);
    }

    #[test]
    fn corrupt_unpublished_journal_keeps_launch_admission_closed_until_repaired() {
        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        let repository = DesktopStateRepository::new(root.path().join("state.json"));
        std::fs::write(repository.unpublished_path(), b"not-json").unwrap();
        let recovery = reconcile_unpublished_startup(&repository, |_| {
            panic!("a corrupt journal must not attempt process cleanup")
        });
        assert_eq!(recovery.health, OwnershipRecoveryState::Untrusted);
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(repository.clone());
            inner.launch_generation = 1;
            inner.unpublished_orphans = recovery.records;
            inner.ownership_recovery = recovery.health;
            inner.snapshot = HostSnapshot::persistence_failure();
        }

        assert_eq!(
            transition_launch_to_starting(&shared, 1, || {}),
            Err("desktop_state_persist_failed")
        );
        assert_eq!(
            super::configuration_action_failure(&shared),
            Some(("desktop_state_persist_failed", "open_diagnostics"))
        );
        assert!(begin_configuration_restart(&shared).is_none());
        assert!(matches!(
            commit_launch_success(
                &shared,
                1,
                OrphanRecord {
                    pid: 42,
                    start_time: 100,
                    executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
                },
                DesktopSession {
                    endpoint: "http://127.0.0.1:4242".into(),
                    bearer: "test-bearer".into(),
                    instance_id: "test-instance".into(),
                },
                (),
                |_, _| {},
            ),
            Err(("desktop_state_persist_failed", _, _))
        ));

        repository.replace_unpublished_orphans(&[]).unwrap();
        let repaired = reconcile_unpublished_startup(&repository, |_| {
            panic!("an empty repaired journal has no process to clean")
        });
        assert_eq!(repaired.health, OwnershipRecoveryState::Healthy);
        {
            let mut inner = shared.0.lock().unwrap();
            inner.unpublished_orphans = repaired.records;
            inner.ownership_recovery = repaired.health;
        }
        assert_eq!(transition_launch_to_starting(&shared, 1, || {}), Ok(()));
        assert_eq!(super::configuration_action_failure(&shared), None);
    }

    #[test]
    fn pending_inserted_after_failure_precheck_cannot_mutate_snapshot_ownership_or_budget() {
        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        let repository = DesktopStateRepository::new(root.path().join("state.json"));
        let runtime = OrphanRecord {
            pid: 99,
            start_time: 200,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        let pending = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: runtime.executable.clone(),
        };
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(repository.clone());
            inner.launch_generation = 7;
            inner.pid = Some(runtime.pid);
            inner.disk.orphan = Some(runtime.clone());
        }

        let inserted_shared = shared.clone();
        let inserted_pending = pending.clone();
        let disposition = record_launch_failure_with_hook(
            &shared,
            7,
            "desktop_sidecar_spawn_failed",
            100,
            move || {
                repository
                    .add_unpublished_orphan(&inserted_pending)
                    .unwrap();
                let mut inner = inserted_shared.0.lock().unwrap();
                inner.unpublished_orphans.push(inserted_pending);
                inner.snapshot = HostSnapshot::problem(
                    "blocked",
                    "desktop_unpublished_sidecar_cleanup_failed",
                    "open_diagnostics",
                );
            },
        );

        assert_eq!(disposition, super::LaunchFailureDisposition::Blocked);
        let inner = shared.0.lock().unwrap();
        assert_eq!(inner.pid, Some(runtime.pid));
        assert_eq!(inner.disk.orphan.as_ref(), Some(&runtime));
        assert_eq!(inner.unpublished_orphans, [pending]);
        assert_eq!(
            serde_json::to_value(&inner.disk.crash_budget).unwrap(),
            serde_json::json!({ "crash_timestamps": [] })
        );
        assert_eq!(
            inner.snapshot.reason_code,
            Some("desktop_unpublished_sidecar_cleanup_failed")
        );
    }

    #[test]
    fn unpublished_cleanup_accepts_stopped_or_reused_identity_as_safe() {
        for result in [
            Ok(TerminationOutcome::Stopped),
            Ok(TerminationOutcome::IdentityChanged),
        ] {
            let root = tempfile::tempdir().unwrap();
            let shared = SharedHostState::default();
            let record = OrphanRecord {
                pid: 42,
                start_time: 100,
                executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
            };
            let repository = DesktopStateRepository::new(root.path().join("state.json"));
            {
                let mut inner = shared.0.lock().unwrap();
                inner.repository = Some(repository.clone());
                super::persist_disk_locked(&inner).unwrap();
            }

            assert!(reserve_unpublished_launch_cleanup(&shared, &record));
            assert!(finish_unpublished_launch_cleanup(&shared, &record, result));
            assert_eq!(repository.load_unpublished_orphans().unwrap(), []);
            assert!(shared.0.lock().unwrap().disk.orphan.is_none());
        }
    }

    #[test]
    fn safe_cleanup_with_failed_exact_remove_retains_ownership_and_health_gate() {
        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        let repository = DesktopStateRepository::new(root.path().join("state.json"));
        let record = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(repository.clone());
            inner.diagnostic_log =
                Some(DiagnosticLog::create(root.path().join("diagnostics")).unwrap());
            inner.launch_generation = 1;
        }
        assert!(reserve_unpublished_launch_cleanup(&shared, &record));
        std::fs::remove_file(repository.unpublished_path()).unwrap();
        std::fs::create_dir(repository.unpublished_path()).unwrap();

        assert!(!finish_unpublished_launch_cleanup(
            &shared,
            &record,
            Ok(TerminationOutcome::Stopped),
        ));

        let inner = shared.0.lock().unwrap();
        assert_eq!(inner.unpublished_orphans, [record]);
        assert_eq!(
            inner.ownership_recovery,
            OwnershipRecoveryState::PersistFailed
        );
        assert_eq!(
            inner.snapshot.reason_code,
            Some("desktop_state_persist_failed")
        );
        drop(inner);
        assert!(begin_configuration_restart(&shared).is_none());
        assert_eq!(
            transition_launch_to_starting(&shared, 1, || {}),
            Err("desktop_state_persist_failed")
        );
    }

    #[test]
    fn startup_reconciliation_safely_removes_and_reloads_unpublished_identity() {
        let root = tempfile::tempdir().unwrap();
        let repository = DesktopStateRepository::new(root.path().join("state.json"));
        let record = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        repository.add_unpublished_orphan(&record).unwrap();

        let recovery =
            reconcile_unpublished_startup(&repository, |_| Ok(TerminationOutcome::IdentityChanged));

        assert_eq!(recovery.health, OwnershipRecoveryState::Healthy);
        assert!(!recovery.cleanup_failed);
        assert!(recovery.records.is_empty());
        assert_eq!(repository.load_unpublished_orphans().unwrap(), []);
    }

    #[test]
    fn startup_verified_reload_failure_or_drift_retains_original_ownership() {
        for mode in ["parse", "io", "drift"] {
            let root = tempfile::tempdir().unwrap();
            let repository = DesktopStateRepository::new(root.path().join("state.json"));
            let record = OrphanRecord {
                pid: 42,
                start_time: 100,
                executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
            };
            repository.add_unpublished_orphan(&record).unwrap();

            let recovery = reconcile_unpublished_startup_with_replace(
                &repository,
                |_| Ok(TerminationOutcome::IdentityChanged),
                |repository, remaining| {
                    repository.replace_unpublished_orphans_verified_with(remaining, |path| {
                        match mode {
                            "parse" => std::fs::write(path, b"not-json"),
                            "io" => {
                                std::fs::remove_file(path)?;
                                std::fs::create_dir(path)
                            }
                            "drift" => std::fs::write(
                                path,
                                serde_json::to_vec(&serde_json::json!({
                                    "unpublished_orphans": [{
                                        "pid": 99,
                                        "start_time": 199,
                                        "executable": "/Applications/Sage.app/Contents/Resources/sidecar/sage-api"
                                    }]
                                }))
                                .map_err(std::io::Error::other)?,
                            ),
                            _ => unreachable!(),
                        }
                    })
                },
            );

            assert_eq!(recovery.health, OwnershipRecoveryState::PersistFailed);
            assert_eq!(recovery.records, [record]);
        }
    }

    #[test]
    fn startup_replace_failure_retains_original_ownership_and_closes_health_gate() {
        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        let repository = DesktopStateRepository::new(root.path().join("state.json"));
        let record = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        repository.add_unpublished_orphan(&record).unwrap();
        let journal_path = repository.unpublished_path().to_path_buf();

        let recovery = reconcile_unpublished_startup(&repository, |_| {
            std::fs::remove_file(&journal_path).unwrap();
            std::fs::create_dir(&journal_path).unwrap();
            Ok(TerminationOutcome::Stopped)
        });
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(repository);
            inner.launch_generation = 1;
            inner.unpublished_orphans = recovery.records;
            inner.ownership_recovery = recovery.health;
            inner.snapshot = HostSnapshot::persistence_failure();
        }

        assert_eq!(recovery.health, OwnershipRecoveryState::PersistFailed);
        assert_eq!(shared.0.lock().unwrap().unpublished_orphans, [record]);
        assert!(begin_configuration_restart(&shared).is_none());
        assert_eq!(
            transition_launch_to_starting(&shared, 1, || {}),
            Err("desktop_state_persist_failed")
        );
    }

    #[test]
    fn startup_unsafe_unpublished_cleanup_uses_dedicated_snapshot_and_diagnostic() {
        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        let repository = DesktopStateRepository::new(root.path().join("state.json"));
        let record = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        repository.add_unpublished_orphan(&record).unwrap();
        let recovery =
            reconcile_unpublished_startup(&repository, |_| Ok(TerminationOutcome::KillSent));
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(repository.clone());
            inner.diagnostic_log =
                Some(DiagnosticLog::create(root.path().join("diagnostics")).unwrap());
            inner.unpublished_orphans = recovery.records;
            inner.ownership_recovery = recovery.health;
        }
        if recovery.cleanup_failed {
            super::set_snapshot(&shared, HostSnapshot::unpublished_termination_failure());
            append_diagnostic(
                &shared,
                "unpublished_sidecar_cleanup_failed",
                "blocked",
                "desktop_unpublished_sidecar_cleanup_failed",
            )
            .unwrap();
        }

        let inner = shared.0.lock().unwrap();
        assert_eq!(
            inner.snapshot.reason_code,
            Some("desktop_unpublished_sidecar_cleanup_failed")
        );
        assert_eq!(inner.snapshot.action, Some("open_diagnostics"));
        assert_eq!(inner.unpublished_orphans, std::slice::from_ref(&record));
        drop(inner);
        assert_eq!(repository.load_unpublished_orphans().unwrap(), [record]);
        let diagnostic =
            std::fs::read_to_string(root.path().join("diagnostics/desktop-host.jsonl")).unwrap();
        assert!(diagnostic.contains("unpublished_sidecar_cleanup_failed"));
    }

    #[test]
    fn stale_launch_starting_transition_cannot_overwrite_new_generation_ready() {
        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(DesktopStateRepository::new(root.path().join("state.json")));
            inner.launch_generation = 1;
        }
        let checked = Arc::new(Barrier::new(2));
        let resume = Arc::new(Barrier::new(2));
        let old_shared = shared.clone();
        let old_checked = checked.clone();
        let old_resume = resume.clone();
        let old = std::thread::spawn(move || {
            transition_launch_to_starting(&old_shared, 1, || {
                old_checked.wait();
                old_resume.wait();
            })
        });

        checked.wait();
        let new_generation = super::next_launch_generation(&shared).unwrap();
        commit_launch_success(
            &shared,
            new_generation,
            OrphanRecord {
                pid: 99,
                start_time: 200,
                executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
            },
            DesktopSession {
                endpoint: "http://127.0.0.1:4999".into(),
                bearer: "new-bearer".into(),
                instance_id: "new-instance".into(),
            },
            (),
            |_, _| {},
        )
        .unwrap();
        resume.wait();

        assert_eq!(old.join().unwrap(), Err("desktop_launch_superseded"));
        let inner = shared.0.lock().unwrap();
        assert_eq!(inner.snapshot.state, "ready");
        assert_eq!(
            inner
                .snapshot
                .session
                .as_ref()
                .map(|session| session.instance_id.as_str()),
            Some("new-instance")
        );
    }

    #[test]
    fn stale_launch_starting_transition_cannot_overwrite_stop_failed_blocked() {
        let shared = SharedHostState::default();
        shared.0.lock().unwrap().launch_generation = 1;
        let checked = Arc::new(Barrier::new(2));
        let resume = Arc::new(Barrier::new(2));
        let old_shared = shared.clone();
        let old_checked = checked.clone();
        let old_resume = resume.clone();
        let old = std::thread::spawn(move || {
            transition_launch_to_starting(&old_shared, 1, || {
                old_checked.wait();
                old_resume.wait();
            })
        });

        checked.wait();
        let restart = begin_configuration_restart(&shared).unwrap();
        mark_configuration_stop_failed(&shared, &restart);
        assert!(finish_configuration_restart(&shared, restart.generation));
        resume.wait();

        assert_eq!(old.join().unwrap(), Err("desktop_launch_superseded"));
        let inner = shared.0.lock().unwrap();
        assert_eq!(inner.snapshot.state, "blocked");
        assert_eq!(
            inner.snapshot.reason_code,
            Some("desktop_sidecar_stop_failed")
        );
        assert_eq!(inner.snapshot.action, Some("open_diagnostics"));
    }

    #[test]
    fn rapid_configuration_restarts_share_one_stop_owner() {
        let shared = SharedHostState::default();
        let record = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        shared.0.lock().unwrap().disk.orphan = Some(record.clone());

        let first = begin_configuration_restart(&shared).unwrap();
        let second = begin_configuration_restart(&shared);

        assert_eq!(first.orphan.as_ref(), Some(&record));
        assert!(second.is_none());
        assert_eq!(shared.0.lock().unwrap().launch_generation, first.generation);
        assert!(finish_configuration_restart(&shared, first.generation));
    }

    #[test]
    fn configuration_mutation_lease_is_invalidated_by_write_ahead_reservation() {
        let root = tempfile::tempdir().unwrap();
        let repository = DesktopStateRepository::new(root.path().join("state.json"));
        let shared = SharedHostState::default();
        shared.0.lock().unwrap().repository = Some(repository.clone());
        let lease = acquire_configuration_mutation_guard(&shared).unwrap();
        let record = OrphanRecord {
            pid: 73,
            start_time: 173,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };

        assert!(reserve_unpublished_launch_cleanup(&shared, &record));
        let committed = lease.commit(false, || Ok::<_, ()>("metadata-visible"));

        assert!(matches!(
            committed,
            Err(ConfigurationMutationCommitError::Admission(_))
        ));
        assert_eq!(repository.load_unpublished_orphans().unwrap(), [record]);
    }

    #[test]
    fn configuration_commit_atomically_owns_one_restart_receipt() {
        let shared = SharedHostState::default();
        let lease = acquire_configuration_mutation_guard(&shared).unwrap();

        let committed = lease
            .commit(true, || Ok::<_, ()>("metadata-visible"))
            .unwrap();

        assert_eq!(committed.value, "metadata-visible");
        assert!(committed.restart.is_some());
        let inner = shared.0.lock().unwrap();
        assert!(inner.configuration_restart_in_progress);
        assert_eq!(inner.snapshot.state, "starting");
        assert_eq!(inner.launch_generation, 1);
        drop(inner);
        assert!(acquire_configuration_mutation_guard(&shared).is_err());
    }

    #[test]
    fn duplicate_restart_after_stop_failure_is_a_pure_noop_until_owner_finishes() {
        let shared = SharedHostState::default();
        let record = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        shared.0.lock().unwrap().disk.orphan = Some(record.clone());
        let owner = begin_configuration_restart(&shared).unwrap();
        mark_configuration_stop_failed(&shared, &owner);

        assert!(begin_configuration_restart(&shared).is_none());

        {
            let inner = shared.0.lock().unwrap();
            assert_eq!(inner.snapshot.state, "blocked");
            assert_eq!(
                inner.snapshot.reason_code,
                Some("desktop_sidecar_stop_failed")
            );
            assert_eq!(inner.snapshot.action, Some("open_diagnostics"));
            assert_eq!(inner.launch_generation, owner.generation);
            assert!(inner.configuration_restart_in_progress);
            assert_eq!(inner.disk.orphan.as_ref(), Some(&record));
        }
        assert!(finish_configuration_restart(&shared, owner.generation));
        assert_eq!(
            shared.0.lock().unwrap().snapshot.reason_code,
            Some("desktop_sidecar_stop_failed")
        );
    }

    #[test]
    fn current_configuration_stop_failure_blocks_but_late_failure_is_a_noop() {
        let shared = SharedHostState::default();
        let current = begin_configuration_restart(&shared).unwrap();

        mark_configuration_stop_failed(&shared, &current);
        assert_eq!(
            shared.0.lock().unwrap().snapshot.reason_code,
            Some("desktop_sidecar_stop_failed")
        );

        let stale = ConfigurationRestartRequest {
            generation: current.generation.wrapping_sub(1),
            orphan: current.orphan.clone(),
        };
        shared.0.lock().unwrap().snapshot =
            HostSnapshot::problem("ready", "new_generation_ready", "none");
        mark_configuration_stop_failed(&shared, &stale);
        assert_eq!(
            shared.0.lock().unwrap().snapshot.reason_code,
            Some("new_generation_ready")
        );
    }

    #[test]
    fn late_generation_stop_completion_is_a_noop_even_for_the_same_orphan() {
        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        let record = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(DesktopStateRepository::new(root.path().join("state.json")));
            inner.launch_generation = 2;
            inner.pid = Some(record.pid);
            inner.disk.orphan = Some(record.clone());
            inner.snapshot = HostSnapshot::problem("ready", "new_generation_ready", "none");
        }

        assert!(!finish_runtime_termination_for_generation(
            &shared,
            &record,
            1,
            Ok(TerminationOutcome::Stopped),
        ));

        let inner = shared.0.lock().unwrap();
        assert_eq!(inner.pid, Some(record.pid));
        assert_eq!(inner.disk.orphan.as_ref(), Some(&record));
        assert_eq!(inner.snapshot.reason_code, Some("new_generation_ready"));
    }

    #[test]
    fn late_stop_completion_is_a_noop_for_new_orphan_ownership() {
        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        let old = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        let current = OrphanRecord {
            pid: 99,
            start_time: 200,
            executable: old.executable.clone(),
        };
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(DesktopStateRepository::new(root.path().join("state.json")));
            inner.pid = Some(current.pid);
            inner.disk.orphan = Some(current.clone());
            inner.snapshot = HostSnapshot::problem("ready", "new_generation_ready", "none");
        }

        assert!(!finish_runtime_termination(
            &shared,
            &old,
            Ok(TerminationOutcome::Stopped),
        ));

        let inner = shared.0.lock().unwrap();
        assert_eq!(inner.pid, Some(current.pid));
        assert_eq!(inner.disk.orphan.as_ref(), Some(&current));
        assert_eq!(inner.snapshot.reason_code, Some("new_generation_ready"));
    }

    #[test]
    fn identity_changed_is_not_a_verified_stop_and_keeps_runtime_blocked() {
        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        let record = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(DesktopStateRepository::new(root.path().join("state.json")));
            inner.pid = Some(record.pid);
            inner.disk.orphan = Some(record.clone());
        }

        assert!(!finish_runtime_termination(
            &shared,
            &record,
            Ok(TerminationOutcome::IdentityChanged),
        ));

        let inner = shared.0.lock().unwrap();
        assert_eq!(inner.disk.orphan.as_ref(), Some(&record));
        assert_eq!(inner.snapshot.state, "blocked");
        assert_eq!(
            inner.snapshot.reason_code,
            Some("desktop_sidecar_stop_failed")
        );
    }

    #[test]
    fn startup_cleanup_preserves_orphan_without_a_verified_stopped_outcome() {
        for outcome in [
            TerminationOutcome::IdentityChanged,
            TerminationOutcome::KillSent,
        ] {
            let record = OrphanRecord {
                pid: 42,
                start_time: 100,
                executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
            };
            let mut disk = crate::state_repository::HostDiskState {
                orphan: Some(record.clone()),
                ..Default::default()
            };

            assert!(apply_startup_termination_outcome(&mut disk, outcome).is_err());
            assert_eq!(disk.orphan.as_ref(), Some(&record));
        }
    }

    #[test]
    fn successful_stop_with_failed_ownership_persist_keeps_orphan_and_blocks_relaunch() {
        let root = tempfile::tempdir().unwrap();
        let target = root.path().join("state.json");
        std::fs::create_dir(&target).unwrap();
        let shared = SharedHostState::default();
        let record = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(DesktopStateRepository::new(target));
            inner.disk.orphan = Some(record.clone());
        }

        assert!(!finish_runtime_termination(
            &shared,
            &record,
            Ok(TerminationOutcome::Stopped),
        ));
        let inner = shared.0.lock().unwrap();
        assert_eq!(inner.disk.orphan.as_ref(), Some(&record));
        assert_eq!(
            inner.snapshot.reason_code,
            Some("desktop_state_persist_failed")
        );
    }

    #[test]
    fn ownership_persistence_can_converge_on_a_later_retry() {
        let root = tempfile::tempdir().unwrap();
        let target = root.path().join("state.json");
        std::fs::create_dir(&target).unwrap();
        let shared = SharedHostState::default();
        let record = OrphanRecord {
            pid: 42,
            start_time: 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        };
        {
            let mut inner = shared.0.lock().unwrap();
            inner.repository = Some(DesktopStateRepository::new(target.clone()));
            inner.disk.orphan = Some(record.clone());
        }
        assert!(!finish_runtime_termination(
            &shared,
            &record,
            Ok(TerminationOutcome::Stopped),
        ));
        std::fs::remove_dir(&target).unwrap();

        assert!(finish_runtime_termination(
            &shared,
            &record,
            Ok(TerminationOutcome::Stopped),
        ));
        assert!(shared.0.lock().unwrap().disk.orphan.is_none());
        let persisted: serde_json::Value =
            serde_json::from_slice(&std::fs::read(target).unwrap()).unwrap();
        assert!(persisted["orphan"].is_null());
    }

    #[test]
    fn diagnostic_log_contains_only_the_public_allowlist() {
        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        shared.0.lock().unwrap().diagnostic_log =
            Some(DiagnosticLog::create(root.path().to_path_buf()).unwrap());

        append_diagnostic(
            &shared,
            "sidecar_crash",
            "degraded",
            "desktop_sidecar_crashed",
        )
        .unwrap();

        let payload: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(root.path().join("desktop-host.jsonl")).unwrap(),
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
        assert!(!payload.to_string().contains("bearer"));
        assert!(!payload.to_string().contains("nonce"));
        assert!(!payload.to_string().contains("endpoint"));
    }
}
