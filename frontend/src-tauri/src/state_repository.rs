use crate::lifecycle::{atomic_write_private, CrashBudget, OrphanRecord};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Default, Deserialize, Serialize)]
pub struct HostDiskState {
    pub crash_budget: CrashBudget,
    pub orphan: Option<OrphanRecord>,
}

#[derive(Clone)]
pub struct DesktopStateRepository {
    path: PathBuf,
}

impl DesktopStateRepository {
    pub fn new(path: PathBuf) -> Self {
        Self { path }
    }

    pub fn load(&self) -> HostDiskState {
        std::fs::read(&self.path)
            .ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .unwrap_or_default()
    }

    pub fn save(&self, state: &HostDiskState) -> std::io::Result<()> {
        let bytes = serde_json::to_vec(state).map_err(std::io::Error::other)?;
        atomic_write_private(&self.path, &bytes)
    }

    pub fn path(&self) -> &Path {
        &self.path
    }
}
