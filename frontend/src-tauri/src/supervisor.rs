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
}

struct HostInner {
    snapshot: HostSnapshot,
    stopping: bool,
    pid: Option<u32>,
    child: Option<CommandChild>,
    repository: Option<DesktopStateRepository>,
    diagnostic_log: Option<DiagnosticLog>,
    disk: HostDiskState,
    launch_generation: u64,
    configuration_restart_in_progress: bool,
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
            launch_generation: 0,
            configuration_restart_in_progress: false,
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
    let cleanup_result = clean_known_orphan(&mut disk);
    let startup = startup_action(&mut disk.crash_budget, unix_seconds());
    let persisted = {
        let mut inner = shared.0.lock().expect("host state poisoned");
        inner.repository = Some(repository);
        inner.diagnostic_log = Some(diagnostic_log);
        inner.disk = disk;
        persist_disk_locked(&inner)
    };
    if persisted.is_err() {
        set_snapshot(&shared, HostSnapshot::persistence_failure());
        let _ = append_diagnostic(
            &shared,
            "state_persist_failed",
            "blocked",
            "desktop_state_persist_failed",
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
    let generation = next_launch_generation(&shared);
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
        if shared.is_stopping() {
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
    if !is_current_generation(shared, generation) {
        return Err("desktop_launch_superseded");
    }
    set_snapshot(shared, HostSnapshot::starting());
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
    if shared.is_stopping() {
        let _ = child.kill();
        return Err("desktop_stopping");
    }
    if !is_current_generation(shared, generation) {
        let _ = child.kill();
        return Err("desktop_launch_superseded");
    }
    if let Err(reason) = persist_orphan(shared, child_pid) {
        let _ = child.kill();
        return Err(reason);
    }
    if shared.is_stopping() {
        let record = {
            shared
                .0
                .lock()
                .expect("host state poisoned")
                .disk
                .orphan
                .clone()
        };
        if let Some(record) = record {
            let _child = child;
            let result = terminate_runtime(&record);
            finish_runtime_termination(shared, &record, result);
        } else {
            let _ = child.kill();
        }
        return Err("desktop_stopping");
    }
    {
        let mut inner = shared.0.lock().expect("host state poisoned");
        inner.pid = Some(child_pid);
        inner.child = Some(child);
        inner.snapshot = HostSnapshot::ready(DesktopSession {
            endpoint,
            bearer,
            instance_id,
        });
    }
    Ok(receiver)
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
        _ => None,
    }
}

fn record_launch_failure(
    shared: &SharedHostState,
    generation: u64,
    reason: &'static str,
    now: u64,
) -> LaunchFailureDisposition {
    if reason == "desktop_launch_superseded" {
        return LaunchFailureDisposition::Superseded;
    }
    if let Some(action) = recoverable_configuration_action(reason) {
        let persistence_failed = {
            let mut inner = shared.0.lock().expect("host state poisoned");
            if inner.launch_generation != generation {
                return LaunchFailureDisposition::Superseded;
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

fn next_launch_generation(shared: &SharedHostState) -> u64 {
    let mut inner = shared.0.lock().expect("host state poisoned");
    inner.launch_generation = inner.launch_generation.wrapping_add(1);
    inner.launch_generation
}

fn is_current_generation(shared: &SharedHostState, generation: u64) -> bool {
    shared
        .0
        .lock()
        .expect("host state poisoned")
        .launch_generation
        == generation
}

pub fn restart_for_configuration(app: AppHandle, shared: SharedHostState) {
    let Ok(data_dir) = app.path().app_data_dir() else {
        set_problem(
            &shared,
            "blocked",
            "desktop_data_dir_unavailable",
            "restart_sage",
        );
        return;
    };
    let Some(request) = begin_configuration_restart(&shared) else {
        return;
    };
    tauri::async_runtime::spawn(async move {
        let stopped = stop_sidecar_for_configuration(&shared, &request).await;
        if !stopped {
            mark_configuration_stop_failed(&shared, &request);
        }
        let completed = finish_configuration_restart(&shared, request.generation);
        if stopped
            && completed
            && !shared.is_stopping()
            && is_current_generation(&shared, request.generation)
        {
            schedule_launch(app, shared, data_dir, Duration::ZERO, request.generation);
        }
    });
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ConfigurationRestartRequest {
    generation: u64,
    orphan: Option<OrphanRecord>,
}

fn begin_configuration_restart(shared: &SharedHostState) -> Option<ConfigurationRestartRequest> {
    let mut inner = shared.0.lock().expect("host state poisoned");
    inner.snapshot = HostSnapshot::starting();
    if inner.configuration_restart_in_progress {
        return None;
    }
    inner.launch_generation = inner.launch_generation.wrapping_add(1);
    inner.configuration_restart_in_progress = true;
    Some(ConfigurationRestartRequest {
        generation: inner.launch_generation,
        orphan: inner.disk.orphan.clone(),
    })
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

fn persist_orphan(shared: &SharedHostState, pid: u32) -> Result<(), &'static str> {
    let Some(observed) = observe_process(pid) else {
        return Err("desktop_process_identity_unavailable");
    };
    let mut inner = shared.0.lock().expect("host state poisoned");
    inner.disk.orphan = Some(OrphanRecord {
        pid: observed.pid,
        start_time: observed.start_time,
        executable: observed.executable,
    });
    persist_disk_locked(&inner).map_err(|_| "desktop_state_persist_failed")
}

fn clean_known_orphan(disk: &mut HostDiskState) -> std::io::Result<()> {
    let Some(record) = disk.orphan.as_ref() else {
        return Ok(());
    };
    let outcome = terminate_runtime(record)?;
    apply_startup_termination_outcome(disk, outcome)
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
        append_diagnostic, apply_startup_termination_outcome, begin_configuration_restart,
        finish_configuration_restart, finish_runtime_termination,
        finish_runtime_termination_for_generation, mark_configuration_stop_failed,
        origin_for_profile, post_handshake_reject_reason, record_launch_failure, runtime_origin,
        ConfigurationRestartRequest, DesktopStateRepository, DiagnosticLog, HostSnapshot,
        SharedHostState, DEVELOPMENT_ORIGIN, PRODUCTION_ORIGIN,
    };
    use crate::lifecycle::{OrphanRecord, TerminationOutcome};
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
