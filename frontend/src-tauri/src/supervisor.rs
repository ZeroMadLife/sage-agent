use crate::lifecycle::{
    can_clean_orphan, startup_action, CrashBudget, ObservedProcess, OrphanRecord, StartupAction,
};
use crate::protocol::{validate_handshake, DesktopSession, ExpectedHandshake, Handshake};
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use rand::RngCore;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fs;
use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use sysinfo::{Pid, ProcessesToUpdate, Signal, System};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use uuid::Uuid;

const API_VERSION: &str = "1";
const ORIGIN: &str = "tauri://localhost";
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
    origin: &'static str,
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

pub fn request_exit(app: AppHandle, shared: SharedHostState) {
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
    let mut disk = load_disk_state(&disk_path);
    clean_known_orphan(&mut disk);
    let startup = startup_action(&mut disk.crash_budget, unix_seconds());
    {
        let mut inner = shared.0.lock().expect("host state poisoned");
        inner.disk_path = Some(disk_path);
        inner.disk = disk;
        persist_disk_locked(&inner);
    }
    if startup == StartupAction::Blocked {
        set_problem(
            &shared,
            "blocked",
            "desktop_crash_budget_exhausted",
            "open_diagnostics",
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
            Err(reason) => handle_crash(app, shared, data_dir, reason),
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
    let command = app
        .shell()
        .sidecar("sage-api")
        .map_err(|_| "desktop_sidecar_missing")?
        .args(["--desktop-host"]);
    let (mut receiver, mut child) = command
        .spawn()
        .map_err(|_| "desktop_sidecar_spawn_failed")?;
    let child_pid = child.pid();
    let bootstrap = Bootstrap {
        instance_id: &instance_id,
        nonce: &nonce,
        bearer: &bearer,
        origin: ORIGIN,
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
    if probe_health(handshake.port, &bearer, &expected.build_sha).is_err() {
        let _ = child.kill();
        return Err("desktop_health_rejected");
    }
    if !persist_orphan(shared, child_pid) {
        let _ = child.kill();
        return Err("desktop_process_identity_unavailable");
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
    let delay = {
        let mut inner = shared.0.lock().expect("host state poisoned");
        inner.pid = None;
        inner.child = None;
        inner.disk.orphan = None;
        let result = inner.disk.crash_budget.record_crash(unix_seconds());
        persist_disk_locked(&inner);
        if result.is_some() {
            inner.snapshot = HostSnapshot::problem("degraded", reason, "wait_for_restart");
        } else {
            inner.snapshot = HostSnapshot::problem(
                "blocked",
                "desktop_crash_budget_exhausted",
                "open_diagnostics",
            );
        }
        result
    };
    if let Some(seconds) = delay {
        schedule_launch(app, shared, data_dir, Duration::from_secs(seconds));
    }
}

async fn stop_sidecar(shared: &SharedHostState) {
    let (pid, child) = {
        let mut inner = shared.0.lock().expect("host state poisoned");
        (inner.pid, inner.child.take())
    };
    drop(child);
    if let Some(pid) = pid {
        unsafe {
            libc::kill(pid as i32, libc::SIGTERM);
        }
        let deadline = std::time::Instant::now() + EXIT_GRACE;
        while process_exists(pid) && std::time::Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        if process_exists(pid) {
            unsafe {
                libc::kill(pid as i32, libc::SIGKILL);
            }
        }
    }
    let mut inner = shared.0.lock().expect("host state poisoned");
    inner.pid = None;
    inner.disk.orphan = None;
    persist_disk_locked(&inner);
}

fn probe_health(port: u16, bearer: &str, build_sha: &str) -> Result<(), ()> {
    let deadline = std::time::Instant::now() + HEALTH_TIMEOUT;
    loop {
        let live = http_json(port, "/health/live", bearer);
        let ready = http_json(port, "/health/ready", bearer);
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

fn http_json(port: u16, path: &str, bearer: &str) -> Result<Value, ()> {
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
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: {ORIGIN}\r\nAuthorization: Bearer {bearer}\r\nConnection: close\r\n\r\n"
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

fn process_exists(pid: u32) -> bool {
    unsafe { libc::kill(pid as i32, 0) == 0 }
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

fn persist_orphan(shared: &SharedHostState, pid: u32) -> bool {
    let Some(observed) = observe_process(pid) else {
        return false;
    };
    let mut inner = shared.0.lock().expect("host state poisoned");
    inner.disk.orphan = Some(OrphanRecord {
        pid: observed.pid,
        start_time: observed.start_time,
        executable: observed.executable,
    });
    persist_disk_locked(&inner);
    true
}

fn clean_known_orphan(disk: &mut HostDiskState) {
    let Some(record) = disk.orphan.as_ref() else {
        return;
    };
    if let Some(observed) = observe_process(record.pid) {
        if can_clean_orphan(record, &observed) {
            let mut system = System::new();
            let pid = Pid::from_u32(record.pid);
            system.refresh_processes(ProcessesToUpdate::Some(&[pid]), true);
            if let Some(process) = system.process(pid) {
                let _ = process.kill_with(Signal::Term);
            }
            let deadline = std::time::Instant::now() + EXIT_GRACE;
            while process_exists(record.pid) && std::time::Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(50));
            }
            if process_exists(record.pid) {
                unsafe {
                    libc::kill(record.pid as i32, libc::SIGKILL);
                }
            }
        }
    }
    disk.orphan = None;
}

fn load_disk_state(path: &Path) -> HostDiskState {
    fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default()
}

fn persist_disk_locked(inner: &HostInner) {
    let Some(path) = inner.disk_path.as_ref() else {
        return;
    };
    let Ok(bytes) = serde_json::to_vec(&inner.disk) else {
        return;
    };
    let temporary = path.with_extension("tmp");
    if fs::write(&temporary, bytes).is_ok() {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(&temporary, fs::Permissions::from_mode(0o600));
        }
        let _ = fs::rename(temporary, path);
    }
}

#[cfg(test)]
mod tests {
    use super::post_handshake_reject_reason;
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
}
