use crate::lifecycle::{
    atomic_write_private, lifecycle_action, startup_action, terminate_verified, CrashBudget,
    LifecycleAction, LifecycleEvent, ObservedProcess, OrphanRecord, ProcessSignal, StartupAction,
};
use crate::protocol::{validate_handshake, DesktopSession, ExpectedHandshake, Handshake};
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use rand::RngCore;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fs::{self, OpenOptions};
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
}

#[derive(Default, Deserialize, Serialize)]
struct HostDiskState {
    crash_budget: CrashBudget,
    orphan: Option<OrphanRecord>,
}

struct HostInner {
    snapshot: HostSnapshot,
    stopping: bool,
    pid: Option<u32>,
    child: Option<CommandChild>,
    disk_path: Option<PathBuf>,
    diagnostics_dir: Option<PathBuf>,
    disk: HostDiskState,
}

impl Default for HostInner {
    fn default() -> Self {
        Self {
            snapshot: HostSnapshot::starting(),
            stopping: false,
            pid: None,
            child: None,
            disk_path: None,
            diagnostics_dir: None,
            disk: HostDiskState::default(),
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
        .diagnostics_dir
        .clone()
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
    let disk_path = data_dir.join("desktop-host-state.json");
    let diagnostics_dir = data_dir.join("diagnostics");
    if fs::create_dir_all(&diagnostics_dir).is_err() {
        set_problem(
            &shared,
            "blocked",
            "desktop_diagnostics_unavailable",
            "restart_sage",
        );
        return;
    }
    let mut disk = load_disk_state(&disk_path);
    let cleanup_result = clean_known_orphan(&mut disk);
    let startup = startup_action(&mut disk.crash_budget, unix_seconds());
    let persisted = {
        let mut inner = shared.0.lock().expect("host state poisoned");
        inner.disk_path = Some(disk_path);
        inner.diagnostics_dir = Some(diagnostics_dir);
        inner.disk = disk;
        persist_disk_locked(&inner)
    };
    if cleanup_result.is_err() || persisted.is_err() {
        set_snapshot(&shared, HostSnapshot::persistence_failure());
        let _ = append_diagnostic(
            &shared,
            "state_persist_failed",
            "blocked",
            "desktop_state_persist_failed",
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
    schedule_launch(app, shared, data_dir, Duration::ZERO);
}

fn schedule_launch(app: AppHandle, shared: SharedHostState, data_dir: PathBuf, delay: Duration) {
    tauri::async_runtime::spawn(async move {
        if !delay.is_zero() {
            tokio::time::sleep(delay).await;
        }
        if shared.is_stopping() {
            return;
        }
        match launch_once(&app, &shared, &data_dir).await {
            Ok(receiver) => monitor(app, shared, data_dir, receiver).await,
            Err(reason) if !shared.is_stopping() => handle_crash(app, shared, data_dir, reason),
            Err(_) => {}
        }
    });
}

async fn launch_once(
    app: &AppHandle,
    shared: &SharedHostState,
    data_dir: &Path,
) -> Result<tauri::async_runtime::Receiver<CommandEvent>, &'static str> {
    set_snapshot(shared, HostSnapshot::starting());
    let instance_id = Uuid::new_v4().to_string();
    let nonce = random_secret();
    let bearer = random_secret();
    let origin = runtime_origin();
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
    };
    let mut encoded = serde_json::to_vec(&bootstrap).map_err(|_| "desktop_bootstrap_failed")?;
    encoded.push(b'\n');
    if child.write(&encoded).is_err() {
        let _ = child.kill();
        return Err("desktop_bootstrap_failed");
    }

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
            let _ = terminate_runtime(&record);
        } else {
            let _ = child.kill();
        }
        let mut inner = shared.0.lock().expect("host state poisoned");
        inner.disk.orphan = None;
        let _ = persist_disk_locked(&inner);
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
    if !shared.is_stopping() {
        handle_crash(app, shared, data_dir, reason);
    }
}

fn handle_crash(app: AppHandle, shared: SharedHostState, data_dir: PathBuf, reason: &'static str) {
    if lifecycle_action(LifecycleEvent::SidecarCrash) != LifecycleAction::RestartWithBackoff {
        return;
    }
    let (delay, persistence_failed) = {
        let mut inner = shared.0.lock().expect("host state poisoned");
        inner.pid = None;
        inner.child = None;
        inner.disk.orphan = None;
        let result = inner.disk.crash_budget.record_crash(unix_seconds());
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
        &shared,
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
    if let Some(seconds) = delay {
        schedule_launch(app, shared, data_dir, Duration::from_secs(seconds));
    }
}

async fn stop_sidecar(shared: &SharedHostState) {
    let owned = shared.clone();
    let _ = tauri::async_runtime::spawn_blocking(move || stop_sidecar_blocking(&owned)).await;
}

fn stop_sidecar_blocking(shared: &SharedHostState) {
    let (record, child) = {
        let mut inner = shared.0.lock().expect("host state poisoned");
        (inner.disk.orphan.clone(), inner.child.take())
    };
    if let Some(record) = record {
        let _child = child;
        let _ = terminate_runtime(&record);
    } else {
        drop(child);
    }
    let mut inner = shared.0.lock().expect("host state poisoned");
    inner.pid = None;
    inner.disk.orphan = None;
    if persist_disk_locked(&inner).is_err() {
        inner.snapshot = HostSnapshot::persistence_failure();
    }
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
    terminate_runtime(record)?;
    disk.orphan = None;
    Ok(())
}

fn load_disk_state(path: &Path) -> HostDiskState {
    fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default()
}

fn persist_disk_locked(inner: &HostInner) -> std::io::Result<()> {
    let Some(path) = inner.disk_path.as_ref() else {
        return Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "desktop state path unavailable",
        ));
    };
    let bytes = serde_json::to_vec(&inner.disk).map_err(std::io::Error::other)?;
    atomic_write_private(path, &bytes)
}

fn terminate_runtime(record: &OrphanRecord) -> std::io::Result<()> {
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
    )?;
    Ok(())
}

#[derive(Serialize)]
struct DiagnosticRecord<'a> {
    timestamp: u64,
    event: &'a str,
    state: &'a str,
    reason_code: &'a str,
}

fn append_diagnostic(
    shared: &SharedHostState,
    event: &str,
    state: &str,
    reason_code: &str,
) -> std::io::Result<()> {
    let path = shared
        .0
        .lock()
        .expect("host state poisoned")
        .diagnostics_dir
        .as_ref()
        .map(|directory| directory.join("desktop-host.jsonl"));
    let Some(path) = path else {
        return Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "desktop diagnostics path unavailable",
        ));
    };
    let record = DiagnosticRecord {
        timestamp: unix_seconds(),
        event,
        state,
        reason_code,
    };
    let mut encoded = serde_json::to_vec(&record).map_err(std::io::Error::other)?;
    encoded.push(b'\n');
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(fs::Permissions::from_mode(0o600))?;
    }
    file.write_all(&encoded)?;
    file.sync_all()
}

#[cfg(test)]
mod tests {
    use super::{
        append_diagnostic, origin_for_profile, post_handshake_reject_reason, runtime_origin,
        HostSnapshot, SharedHostState, DEVELOPMENT_ORIGIN, PRODUCTION_ORIGIN,
    };
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
    fn diagnostic_log_contains_only_the_public_allowlist() {
        let root = tempfile::tempdir().unwrap();
        let shared = SharedHostState::default();
        shared.0.lock().unwrap().diagnostics_dir = Some(root.path().to_path_buf());

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
