use crate::lifecycle::{atomic_write_private, CrashBudget, OrphanRecord};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

#[derive(Default, Deserialize, Serialize)]
pub struct HostDiskState {
    pub crash_budget: CrashBudget,
    pub orphan: Option<OrphanRecord>,
}

#[derive(Clone)]
pub struct DesktopStateRepository {
    path: PathBuf,
    unpublished_path: PathBuf,
    unpublished_lock: Arc<Mutex<()>>,
}

impl DesktopStateRepository {
    pub fn new(path: PathBuf) -> Self {
        let unpublished_path = path.with_file_name("desktop-host-unpublished-orphans.json");
        Self {
            path,
            unpublished_path,
            unpublished_lock: Arc::new(Mutex::new(())),
        }
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

    pub fn unpublished_path(&self) -> &Path {
        &self.unpublished_path
    }

    pub fn load_unpublished_orphans(&self) -> std::io::Result<Vec<OrphanRecord>> {
        let _guard = self
            .unpublished_lock
            .lock()
            .expect("unpublished orphan state poisoned");
        self.load_unpublished_orphans_unlocked()
            .map(|state| state.unpublished_orphans)
    }

    pub fn add_unpublished_orphan(&self, record: &OrphanRecord) -> std::io::Result<()> {
        let _guard = self
            .unpublished_lock
            .lock()
            .expect("unpublished orphan state poisoned");
        let mut state = self.load_unpublished_orphans_unlocked()?;
        if !state.unpublished_orphans.contains(record) {
            state.unpublished_orphans.push(record.clone());
        }
        self.save_unpublished_orphans_unlocked(&state.unpublished_orphans)
    }

    pub fn replace_unpublished_orphans(&self, records: &[OrphanRecord]) -> std::io::Result<()> {
        let _guard = self
            .unpublished_lock
            .lock()
            .expect("unpublished orphan state poisoned");
        self.save_unpublished_orphans_unlocked(records)
    }

    fn load_unpublished_orphans_unlocked(&self) -> std::io::Result<UnpublishedOrphanState> {
        match std::fs::read(&self.unpublished_path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(std::io::Error::other),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                Ok(UnpublishedOrphanState::default())
            }
            Err(error) => Err(error),
        }
    }

    fn save_unpublished_orphans_unlocked(&self, records: &[OrphanRecord]) -> std::io::Result<()> {
        let bytes = serde_json::to_vec(&UnpublishedOrphanState {
            unpublished_orphans: records.to_vec(),
        })
        .map_err(std::io::Error::other)?;
        atomic_write_private(&self.unpublished_path, &bytes)
    }
}

#[derive(Default, Deserialize, Serialize)]
struct UnpublishedOrphanState {
    unpublished_orphans: Vec<OrphanRecord>,
}
