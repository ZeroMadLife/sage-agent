use serde::Serialize;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Clone)]
pub struct DiagnosticLog {
    directory: PathBuf,
}

#[derive(Serialize)]
struct DiagnosticRecord<'a> {
    timestamp: u64,
    event: &'a str,
    state: &'a str,
    reason_code: &'a str,
}

impl DiagnosticLog {
    pub fn create(directory: PathBuf) -> std::io::Result<Self> {
        fs::create_dir_all(&directory)?;
        Ok(Self { directory })
    }

    pub fn directory(&self) -> &Path {
        &self.directory
    }

    pub fn append(&self, event: &str, state: &str, reason_code: &str) -> std::io::Result<()> {
        self.append_at(unix_seconds(), event, state, reason_code)
    }

    pub fn append_at(
        &self,
        timestamp: u64,
        event: &str,
        state: &str,
        reason_code: &str,
    ) -> std::io::Result<()> {
        let record = DiagnosticRecord {
            timestamp,
            event,
            state,
            reason_code,
        };
        let mut encoded = serde_json::to_vec(&record).map_err(std::io::Error::other)?;
        encoded.push(b'\n');
        let path = self.directory.join("desktop-host.jsonl");
        let mut file = OpenOptions::new().create(true).append(true).open(path)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            file.set_permissions(fs::Permissions::from_mode(0o600))?;
        }
        file.write_all(&encoded)?;
        file.sync_all()
    }
}

fn unix_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
