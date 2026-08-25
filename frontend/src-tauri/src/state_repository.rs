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

    pub fn replace_unpublished_orphans_verified(
        &self,
        records: &[OrphanRecord],
    ) -> std::io::Result<Vec<OrphanRecord>> {
        self.replace_unpublished_orphans_verified_with(records, |_| Ok(()))
    }

    pub(crate) fn replace_unpublished_orphans_verified_with<F>(
        &self,
        records: &[OrphanRecord],
        after_replace: F,
    ) -> std::io::Result<Vec<OrphanRecord>>
    where
        F: FnOnce(&Path) -> std::io::Result<()>,
    {
        let _guard = self
            .unpublished_lock
            .lock()
            .expect("unpublished orphan state poisoned");
        self.save_unpublished_orphans_unlocked(records)?;
        after_replace(&self.unpublished_path)?;
        let reloaded = self
            .load_unpublished_orphans_unlocked()?
            .unpublished_orphans;
        if reloaded != records {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "unpublished orphan journal reload did not match replacement",
            ));
        }
        Ok(reloaded)
    }

    pub fn remove_unpublished_orphan(&self, record: &OrphanRecord) -> std::io::Result<()> {
        let _guard = self
            .unpublished_lock
            .lock()
            .expect("unpublished orphan state poisoned");
        let mut state = self.load_unpublished_orphans_unlocked()?;
        let previous_len = state.unpublished_orphans.len();
        state
            .unpublished_orphans
            .retain(|candidate| candidate != record);
        if state.unpublished_orphans.len() == previous_len {
            return Err(std::io::Error::new(
                std::io::ErrorKind::NotFound,
                "unpublished orphan identity is not journaled",
            ));
        }
        self.save_unpublished_orphans_unlocked(&state.unpublished_orphans)
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

#[cfg(test)]
mod tests {
    use super::DesktopStateRepository;
    use crate::lifecycle::OrphanRecord;

    fn record(pid: u32) -> OrphanRecord {
        OrphanRecord {
            pid,
            start_time: u64::from(pid) + 100,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        }
    }

    #[test]
    fn verified_replace_fails_when_reload_is_not_parseable() {
        let root = tempfile::tempdir().unwrap();
        let repository = DesktopStateRepository::new(root.path().join("desktop-host-state.json"));

        let result = repository.replace_unpublished_orphans_verified_with(&[record(42)], |path| {
            std::fs::write(path, b"not-json")
        });

        assert!(result.is_err());
    }

    #[test]
    fn verified_replace_fails_when_reloaded_content_drifts() {
        let root = tempfile::tempdir().unwrap();
        let repository = DesktopStateRepository::new(root.path().join("desktop-host-state.json"));
        let drifted = record(43);

        let result = repository.replace_unpublished_orphans_verified_with(&[record(42)], |path| {
            let bytes = serde_json::to_vec(&serde_json::json!({
                "unpublished_orphans": [drifted]
            }))
            .map_err(std::io::Error::other)?;
            std::fs::write(path, bytes)
        });

        assert!(result.is_err());
    }

    #[test]
    fn verified_replace_fails_when_reload_has_an_io_error() {
        let root = tempfile::tempdir().unwrap();
        let repository = DesktopStateRepository::new(root.path().join("desktop-host-state.json"));

        let result = repository.replace_unpublished_orphans_verified_with(&[record(42)], |path| {
            std::fs::remove_file(path)?;
            std::fs::create_dir(path)
        });

        assert!(result.is_err());
    }
}

#[derive(Default, Deserialize, Serialize)]
struct UnpublishedOrphanState {
    unpublished_orphans: Vec<OrphanRecord>,
}
