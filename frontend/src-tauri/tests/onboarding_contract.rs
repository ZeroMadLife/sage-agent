use sage_desktop_lib::onboarding::{
    CapabilityInputs, DesktopOnboardingAction, HostCapabilityProbe, LocalProviderInput,
    OnboardingMode, OnboardingService, ProviderProbe, ProviderProbeError,
};
use sage_desktop_lib::secret_broker::{SecretBroker, SecretBrokerError};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

#[derive(Default)]
struct MemorySecrets(Mutex<HashMap<String, String>>);

impl SecretBroker for MemorySecrets {
    fn key_ref(&self, account: &str) -> Result<String, SecretBrokerError> {
        Ok(format!("memory://{account}"))
    }

    fn store(&self, account: &str, secret: &str) -> Result<String, SecretBrokerError> {
        let key_ref = self.key_ref(account)?;
        self.0
            .lock()
            .unwrap()
            .insert(key_ref.clone(), secret.to_string());
        Ok(key_ref)
    }

    fn read(&self, key_ref: &str) -> Result<String, SecretBrokerError> {
        self.0
            .lock()
            .unwrap()
            .get(key_ref)
            .cloned()
            .ok_or(SecretBrokerError::Missing)
    }

    fn delete(&self, key_ref: &str) -> Result<(), SecretBrokerError> {
        self.0
            .lock()
            .unwrap()
            .remove(key_ref)
            .map(|_| ())
            .ok_or(SecretBrokerError::Missing)
    }
}

#[derive(Default)]
struct FailurePlan {
    store: Option<SecretBrokerError>,
    read: Option<SecretBrokerError>,
    delete: Option<SecretBrokerError>,
}

#[derive(Default)]
struct ScriptedSecrets {
    values: Mutex<HashMap<String, String>>,
    failures: Mutex<FailurePlan>,
}

impl ScriptedSecrets {
    fn fail_store(&self, error: SecretBrokerError) {
        self.failures.lock().unwrap().store = Some(error);
    }

    fn fail_read(&self, error: SecretBrokerError) {
        self.failures.lock().unwrap().read = Some(error);
    }

    fn fail_delete(&self, error: SecretBrokerError) {
        self.failures.lock().unwrap().delete = Some(error);
    }

    fn contains(&self, key_ref: &str) -> bool {
        self.values.lock().unwrap().contains_key(key_ref)
    }
}

impl SecretBroker for ScriptedSecrets {
    fn key_ref(&self, account: &str) -> Result<String, SecretBrokerError> {
        Ok(format!("scripted://{account}"))
    }

    fn store(&self, account: &str, secret: &str) -> Result<String, SecretBrokerError> {
        if let Some(error) = self.failures.lock().unwrap().store.take() {
            return Err(error);
        }
        let key_ref = self.key_ref(account)?;
        self.values
            .lock()
            .unwrap()
            .insert(key_ref.clone(), secret.to_string());
        Ok(key_ref)
    }

    fn read(&self, key_ref: &str) -> Result<String, SecretBrokerError> {
        if let Some(error) = self.failures.lock().unwrap().read.take() {
            return Err(error);
        }
        self.values
            .lock()
            .unwrap()
            .get(key_ref)
            .cloned()
            .ok_or(SecretBrokerError::Missing)
    }

    fn delete(&self, key_ref: &str) -> Result<(), SecretBrokerError> {
        if let Some(error) = self.failures.lock().unwrap().delete.take() {
            return Err(error);
        }
        self.values
            .lock()
            .unwrap()
            .remove(key_ref)
            .map(|_| ())
            .ok_or(SecretBrokerError::Missing)
    }
}

#[derive(Default)]
struct FixedProviderProbe;

impl ProviderProbe for FixedProviderProbe {
    fn discover_models(
        &self,
        _base_url: &str,
        secret: &str,
    ) -> Result<Vec<String>, ProviderProbeError> {
        assert!(secret.starts_with("test-secret-"));
        Ok(vec!["model-small".into(), "model-large".into()])
    }
}

struct FixedCapabilities(CapabilityInputs);

impl HostCapabilityProbe for FixedCapabilities {
    fn detect(&self) -> CapabilityInputs {
        self.0
    }
}

fn no_optional_services() -> Arc<dyn HostCapabilityProbe> {
    Arc::new(FixedCapabilities(CapabilityInputs {
        docker_ready: false,
        postgres_ready: false,
        web_search_ready: false,
    }))
}

#[test]
fn onboarding_action_schema_rejects_secret_fields_outside_provider_writes() {
    let action: DesktopOnboardingAction = serde_json::from_value(serde_json::json!({
        "kind": "add_provider",
        "name": "Provider",
        "base_url": "https://api.openai.com/v1",
        "api_key": "write-only-test-secret",
        "default_model": "model-small"
    }))
    .unwrap();
    assert!(matches!(
        action,
        DesktopOnboardingAction::AddProvider { .. }
    ));

    assert!(
        serde_json::from_value::<DesktopOnboardingAction>(serde_json::json!({
            "kind": "choose_mode",
            "mode": "local",
            "api_key": "must-be-rejected"
        }))
        .is_err()
    );
}

#[test]
fn local_onboarding_rebuilds_from_sqlite_without_persisting_the_secret() {
    let root = tempfile::tempdir().unwrap();
    let workspace = root.path().join("workspace with spaces");
    std::fs::create_dir(&workspace).unwrap();
    let secrets = Arc::new(MemorySecrets::default());
    let mut service = OnboardingService::open_with(
        root.path().join("data"),
        secrets.clone(),
        Arc::new(FixedProviderProbe),
        no_optional_services(),
    )
    .unwrap();

    assert_eq!(service.snapshot().stage, "choose_mode");
    service.choose_mode(OnboardingMode::Local).unwrap();
    assert_eq!(service.snapshot().stage, "select_workspace");
    service.select_workspace(&workspace).unwrap();
    assert_eq!(service.snapshot().stage, "configure_provider");

    let secret = "test-secret-never-persist";
    let provider = service
        .add_provider(LocalProviderInput {
            name: "Local OpenAI".into(),
            base_url: "https://api.openai.com/v1".into(),
            api_key: secret.into(),
            default_model: "model-small".into(),
        })
        .unwrap();
    service.probe_provider(&provider.provider_id).unwrap();
    service
        .set_default_model(&provider.provider_id, "model-large")
        .unwrap();

    let snapshot = service.snapshot();
    assert_eq!(snapshot.stage, "complete");
    assert_eq!(snapshot.status, "degraded");
    assert_eq!(snapshot.capabilities["conversation"].status, "ready");
    assert_eq!(snapshot.capabilities["rag"].status, "ready");
    assert_eq!(snapshot.capabilities["side_effect_tools"].status, "blocked");
    assert_eq!(snapshot.capabilities["postgres"].status, "degraded");
    assert_eq!(snapshot.capabilities["web_search"].status, "degraded");
    assert_eq!(
        snapshot.providers[0].default_model.as_deref(),
        Some("model-large")
    );
    assert!(!serde_json::to_string(&snapshot).unwrap().contains(secret));

    let database = std::fs::read(service.database_path()).unwrap();
    assert!(!String::from_utf8_lossy(&database).contains(secret));
    assert!(std::env::vars().all(|(_, value)| value != secret));
    for entry in std::fs::read_dir(root.path().join("data")).unwrap() {
        let path = entry.unwrap().path();
        if path.is_file() {
            assert!(!String::from_utf8_lossy(&std::fs::read(path).unwrap()).contains(secret));
        }
    }

    drop(service);
    let rebuilt = OnboardingService::open_with(
        root.path().join("data"),
        secrets,
        Arc::new(FixedProviderProbe),
        no_optional_services(),
    )
    .unwrap()
    .snapshot();
    assert_eq!(rebuilt.stage, "complete");
    assert_eq!(rebuilt.providers[0].key_ref, provider.key_ref);
    assert_eq!(rebuilt.providers[0].key_hint, "****sist");
}

#[test]
fn completed_onboarding_rebuilds_one_write_only_runtime_configuration() {
    let root = tempfile::tempdir().unwrap();
    let workspace = root.path().join("workspace");
    std::fs::create_dir(&workspace).unwrap();
    let secrets = Arc::new(MemorySecrets::default());
    let mut service = OnboardingService::open_with(
        root.path().join("data"),
        secrets,
        Arc::new(FixedProviderProbe),
        no_optional_services(),
    )
    .unwrap();
    service.choose_mode(OnboardingMode::Local).unwrap();
    service.select_workspace(&workspace).unwrap();
    let provider = service
        .add_provider(LocalProviderInput {
            name: "Provider".into(),
            base_url: "https://provider.example/v1".into(),
            api_key: "test-secret-runtime-only".into(),
            default_model: "model-small".into(),
        })
        .unwrap();
    service.probe_provider(&provider.provider_id).unwrap();

    let runtime = service.runtime_configuration().unwrap().unwrap();

    assert_eq!(runtime.workspace_path(), workspace.canonicalize().unwrap());
    assert_eq!(runtime.provider_id(), provider.provider_id);
    assert_eq!(runtime.base_url(), "https://provider.example/v1");
    assert_eq!(runtime.default_model(), "model-small");
    assert_eq!(runtime.api_mode(), "openai_chat_completions");
    assert_eq!(runtime.sandbox_provider(), "local_workspace");
    assert!(!runtime.side_effect_tools_enabled());
}

#[test]
fn keychain_failures_keep_provider_metadata_recoverable() {
    let root = tempfile::tempdir().unwrap();
    let workspace = root.path().join("workspace");
    std::fs::create_dir(&workspace).unwrap();
    let secrets = Arc::new(ScriptedSecrets::default());
    let mut service = OnboardingService::open_with(
        root.path().join("data"),
        secrets.clone(),
        Arc::new(FixedProviderProbe),
        no_optional_services(),
    )
    .unwrap();
    service.choose_mode(OnboardingMode::Local).unwrap();
    service.select_workspace(&workspace).unwrap();

    secrets.fail_store(SecretBrokerError::AccessDenied);
    let denied = service
        .add_provider(LocalProviderInput {
            name: "Provider".into(),
            base_url: "https://api.openai.com/v1".into(),
            api_key: "test-secret-denied".into(),
            default_model: "model-small".into(),
        })
        .unwrap_err();
    assert_eq!(denied.reason_code, "keychain_access_denied");
    assert!(service.snapshot().providers.is_empty());

    let provider = service
        .add_provider(LocalProviderInput {
            name: "Provider".into(),
            base_url: "https://api.openai.com/v1".into(),
            api_key: "test-secret-current".into(),
            default_model: "model-small".into(),
        })
        .unwrap();
    service.probe_provider(&provider.provider_id).unwrap();

    secrets.fail_read(SecretBrokerError::Locked);
    let locked_snapshot = service.snapshot();
    assert_eq!(locked_snapshot.status, "blocked");
    assert_eq!(
        locked_snapshot.reason_code.as_deref(),
        Some("keychain_locked")
    );
    assert_eq!(
        locked_snapshot.action.as_deref(),
        Some("unlock_keychain_and_retry")
    );
    assert_eq!(
        locked_snapshot.capabilities["provider"]
            .reason_code
            .as_deref(),
        Some("keychain_locked")
    );

    secrets.fail_read(SecretBrokerError::Locked);
    let locked = service.probe_provider(&provider.provider_id).unwrap_err();
    assert_eq!(locked.reason_code, "keychain_locked");
    assert_eq!(service.snapshot().providers.len(), 1);

    secrets.fail_read(SecretBrokerError::AccessDenied);
    let denied_snapshot = service.snapshot();
    assert_eq!(
        denied_snapshot.reason_code.as_deref(),
        Some("keychain_access_denied")
    );
    assert_eq!(
        denied_snapshot.action.as_deref(),
        Some("allow_keychain_access_and_retry")
    );

    secrets.fail_store(SecretBrokerError::AccessDenied);
    let rotate_denied = service
        .rotate_provider_key(&provider.provider_id, "test-secret-rotated")
        .unwrap_err();
    assert_eq!(rotate_denied.reason_code, "keychain_access_denied");
    assert_eq!(service.snapshot().providers.len(), 1);

    secrets.fail_delete(SecretBrokerError::AccessDenied);
    let disconnect_denied = service
        .disconnect_provider(&provider.provider_id)
        .unwrap_err();
    assert_eq!(
        disconnect_denied.reason_code,
        "provider_reconciliation_required"
    );
    assert!(!service.snapshot().providers[0].key_configured);

    secrets.fail_delete(SecretBrokerError::AccessDenied);
    let delete_denied = service.delete_provider(&provider.provider_id).unwrap_err();
    assert_eq!(
        delete_denied.reason_code,
        "provider_reconciliation_required"
    );
    assert_eq!(service.snapshot().providers.len(), 1);
}

#[test]
fn provider_rotation_disconnect_and_delete_are_recoverable() {
    let root = tempfile::tempdir().unwrap();
    let workspace = root.path().join("workspace");
    std::fs::create_dir(&workspace).unwrap();
    let secrets = Arc::new(MemorySecrets::default());
    let mut service = OnboardingService::open_with(
        root.path().join("data"),
        secrets.clone(),
        Arc::new(FixedProviderProbe),
        no_optional_services(),
    )
    .unwrap();
    service.choose_mode(OnboardingMode::Local).unwrap();
    service.select_workspace(&workspace).unwrap();
    let provider = service
        .add_provider(LocalProviderInput {
            name: "Provider".into(),
            base_url: "https://api.openai.com/v1".into(),
            api_key: "test-secret-old".into(),
            default_model: "model-small".into(),
        })
        .unwrap();

    let rotated = service
        .rotate_provider_key(&provider.provider_id, "test-secret-new")
        .unwrap();
    assert_ne!(rotated.key_ref, provider.key_ref);
    assert_eq!(secrets.read(&rotated.key_ref).unwrap(), "test-secret-new");
    assert_eq!(service.snapshot().providers[0].key_hint, "****-new");

    service.disconnect_provider(&provider.provider_id).unwrap();
    assert!(matches!(
        secrets.read(&rotated.key_ref),
        Err(SecretBrokerError::Missing)
    ));
    assert_eq!(service.snapshot().stage, "configure_provider");

    let restored = service
        .rotate_provider_key(&provider.provider_id, "test-secret-restored")
        .unwrap();
    service.delete_provider(&provider.provider_id).unwrap();
    assert!(service.snapshot().providers.is_empty());
    assert!(matches!(
        secrets.read(&restored.key_ref),
        Err(SecretBrokerError::Missing)
    ));
}

#[test]
fn active_provider_is_explicit_persisted_and_never_silently_reassigned() {
    let root = tempfile::tempdir().unwrap();
    let workspace = root.path().join("workspace");
    std::fs::create_dir(&workspace).unwrap();
    let secrets = Arc::new(MemorySecrets::default());
    let mut service = OnboardingService::open_with(
        root.path().join("data"),
        secrets.clone(),
        Arc::new(FixedProviderProbe),
        no_optional_services(),
    )
    .unwrap();
    service.choose_mode(OnboardingMode::Local).unwrap();
    service.select_workspace(&workspace).unwrap();
    let first = service
        .add_provider(LocalProviderInput {
            name: "First".into(),
            base_url: "https://first.example/v1".into(),
            api_key: "test-secret-first".into(),
            default_model: "model-small".into(),
        })
        .unwrap();
    service.probe_provider(&first.provider_id).unwrap();
    let second = service
        .add_provider(LocalProviderInput {
            name: "Second".into(),
            base_url: "https://second.example/v1".into(),
            api_key: "test-secret-second".into(),
            default_model: "model-small".into(),
        })
        .unwrap();
    service.probe_provider(&second.provider_id).unwrap();

    let snapshot = service.snapshot();
    assert_eq!(
        snapshot.active_provider_id.as_deref(),
        Some(first.provider_id.as_str())
    );
    assert!(
        snapshot
            .providers
            .iter()
            .find(|item| item.provider_id == first.provider_id)
            .unwrap()
            .is_active
    );
    assert!(
        !snapshot
            .providers
            .iter()
            .find(|item| item.provider_id == second.provider_id)
            .unwrap()
            .is_active
    );
    assert_eq!(
        service
            .runtime_configuration()
            .unwrap()
            .unwrap()
            .provider_id(),
        first.provider_id
    );

    service.set_active_provider(&second.provider_id).unwrap();
    service
        .set_default_model(&second.provider_id, "model-large")
        .unwrap();
    service
        .rotate_provider_key(&first.provider_id, "test-secret-first-rotated")
        .unwrap();
    service.probe_provider(&first.provider_id).unwrap();
    assert_eq!(
        service
            .runtime_configuration()
            .unwrap()
            .unwrap()
            .provider_id(),
        second.provider_id
    );
    assert_eq!(
        service
            .runtime_configuration()
            .unwrap()
            .unwrap()
            .default_model(),
        "model-large"
    );

    service.delete_provider(&second.provider_id).unwrap();
    let blocked = service.snapshot();
    assert_eq!(blocked.active_provider_id, None);
    assert_eq!(blocked.status, "blocked");
    assert_eq!(
        blocked.reason_code.as_deref(),
        Some("active_provider_required")
    );
    assert!(service.runtime_configuration().unwrap().is_none());

    drop(service);
    let mut rebuilt = OnboardingService::open_with(
        root.path().join("data"),
        secrets,
        Arc::new(FixedProviderProbe),
        no_optional_services(),
    )
    .unwrap();
    assert_eq!(rebuilt.snapshot().active_provider_id, None);
    rebuilt.set_active_provider(&first.provider_id).unwrap();
    assert_eq!(
        rebuilt
            .runtime_configuration()
            .unwrap()
            .unwrap()
            .provider_id(),
        first.provider_id
    );
}

#[test]
fn provider_action_union_has_an_explicit_set_active_command() {
    let action: DesktopOnboardingAction = serde_json::from_value(serde_json::json!({
        "kind": "set_active_provider",
        "provider_id": "provider-2"
    }))
    .unwrap();
    assert!(matches!(
        action,
        DesktopOnboardingAction::SetActiveProvider { provider_id } if provider_id == "provider-2"
    ));
}

#[test]
fn failed_storage_and_failed_compensation_reconcile_after_restart() {
    let root = tempfile::tempdir().unwrap();
    let data_dir = root.path().join("data");
    let secrets = Arc::new(ScriptedSecrets::default());
    let mut service = OnboardingService::open_with(
        data_dir.clone(),
        secrets.clone(),
        Arc::new(FixedProviderProbe),
        no_optional_services(),
    )
    .unwrap();
    let blocker = rusqlite::Connection::open(service.database_path()).unwrap();
    blocker
        .execute_batch(
            "CREATE TRIGGER fail_provider_insert BEFORE INSERT ON local_providers
         BEGIN SELECT RAISE(FAIL, 'injected metadata failure'); END;",
        )
        .unwrap();
    secrets.fail_delete(SecretBrokerError::AccessDenied);

    let error = service
        .add_provider(LocalProviderInput {
            name: "Interrupted".into(),
            base_url: "https://provider.example/v1".into(),
            api_key: "test-secret-reconcile".into(),
            default_model: "model-small".into(),
        })
        .unwrap_err();
    assert_eq!(error.reason_code, "provider_reconciliation_required");
    assert_eq!(service.pending_operation_count().unwrap(), 1);
    let pending_ref = service.pending_operation_key_refs().unwrap().pop().unwrap();
    assert!(secrets.contains(&pending_ref));

    blocker
        .execute_batch("DROP TRIGGER fail_provider_insert;")
        .unwrap();
    drop(blocker);
    drop(service);
    let rebuilt = OnboardingService::open_with(
        data_dir,
        secrets.clone(),
        Arc::new(FixedProviderProbe),
        no_optional_services(),
    )
    .unwrap();
    assert_eq!(rebuilt.pending_operation_count().unwrap(), 0);
    assert!(!secrets.contains(&pending_ref));
    assert!(rebuilt.snapshot().providers.is_empty());
}

#[test]
fn rotate_cleanup_failure_is_durable_and_restart_finishes_reconciliation() {
    let root = tempfile::tempdir().unwrap();
    let data_dir = root.path().join("data");
    let secrets = Arc::new(ScriptedSecrets::default());
    let mut service = OnboardingService::open_with(
        data_dir.clone(),
        secrets.clone(),
        Arc::new(FixedProviderProbe),
        no_optional_services(),
    )
    .unwrap();
    let provider = service
        .add_provider(LocalProviderInput {
            name: "Provider".into(),
            base_url: "https://provider.example/v1".into(),
            api_key: "test-secret-old".into(),
            default_model: "model-small".into(),
        })
        .unwrap();
    let old_ref = provider.key_ref;
    secrets.fail_delete(SecretBrokerError::AccessDenied);

    let error = service
        .rotate_provider_key(&provider.provider_id, "test-secret-new")
        .unwrap_err();
    assert_eq!(error.reason_code, "provider_reconciliation_required");
    assert_eq!(service.pending_operation_count().unwrap(), 1);
    let new_ref = service.snapshot().providers[0].key_ref.clone();
    assert_ne!(new_ref, old_ref);
    assert!(secrets.contains(&old_ref));
    assert!(secrets.contains(&new_ref));

    drop(service);
    let rebuilt = OnboardingService::open_with(
        data_dir,
        secrets.clone(),
        Arc::new(FixedProviderProbe),
        no_optional_services(),
    )
    .unwrap();
    assert_eq!(rebuilt.pending_operation_count().unwrap(), 0);
    assert!(!secrets.contains(&old_ref));
    assert!(secrets.contains(&new_ref));
}

#[test]
fn disconnect_metadata_failure_reconciles_without_deleting_the_live_secret() {
    let root = tempfile::tempdir().unwrap();
    let data_dir = root.path().join("data");
    let secrets = Arc::new(ScriptedSecrets::default());
    let mut service = OnboardingService::open_with(
        data_dir.clone(),
        secrets.clone(),
        Arc::new(FixedProviderProbe),
        no_optional_services(),
    )
    .unwrap();
    let provider = service
        .add_provider(LocalProviderInput {
            name: "Provider".into(),
            base_url: "https://provider.example/v1".into(),
            api_key: "test-secret-live".into(),
            default_model: "model-small".into(),
        })
        .unwrap();
    service.probe_provider(&provider.provider_id).unwrap();
    let blocker = rusqlite::Connection::open(service.database_path()).unwrap();
    blocker
        .execute_batch(
            "CREATE TRIGGER fail_provider_disconnect BEFORE UPDATE ON local_providers
         WHEN NEW.status = 'disconnected'
         BEGIN SELECT RAISE(FAIL, 'injected disconnect failure'); END;",
        )
        .unwrap();

    let error = service
        .disconnect_provider(&provider.provider_id)
        .unwrap_err();
    assert_eq!(error.reason_code, "desktop_metadata_unavailable");
    assert_eq!(service.pending_operation_count().unwrap(), 1);
    assert!(secrets.contains(&provider.key_ref));

    blocker
        .execute_batch("DROP TRIGGER fail_provider_disconnect;")
        .unwrap();
    drop(blocker);
    drop(service);
    let rebuilt = OnboardingService::open_with(
        data_dir,
        secrets.clone(),
        Arc::new(FixedProviderProbe),
        no_optional_services(),
    )
    .unwrap();
    assert_eq!(rebuilt.pending_operation_count().unwrap(), 0);
    assert!(secrets.contains(&provider.key_ref));
    assert!(rebuilt.snapshot().providers[0].key_configured);
}

#[test]
fn pending_reconciliation_blocks_followup_provider_mutations_that_could_orphan_secrets() {
    let root = tempfile::tempdir().unwrap();
    let data_dir = root.path().join("data");
    let secrets = Arc::new(ScriptedSecrets::default());
    let mut service = OnboardingService::open_with(
        data_dir.clone(),
        secrets.clone(),
        Arc::new(FixedProviderProbe),
        no_optional_services(),
    )
    .unwrap();
    let provider = service
        .add_provider(LocalProviderInput {
            name: "Provider".into(),
            base_url: "https://provider.example/v1".into(),
            api_key: "test-secret-live".into(),
            default_model: "model-small".into(),
        })
        .unwrap();
    secrets.fail_delete(SecretBrokerError::AccessDenied);
    assert_eq!(
        service
            .delete_provider(&provider.provider_id)
            .unwrap_err()
            .reason_code,
        "provider_reconciliation_required"
    );

    let error = service
        .rotate_provider_key(&provider.provider_id, "test-secret-must-not-be-stored")
        .unwrap_err();

    assert_eq!(error.reason_code, "provider_reconciliation_required");
    assert_eq!(service.pending_operation_count().unwrap(), 1);
    assert!(secrets.contains(&provider.key_ref));

    drop(service);
    let rebuilt = OnboardingService::open_with(
        data_dir,
        secrets.clone(),
        Arc::new(FixedProviderProbe),
        no_optional_services(),
    )
    .unwrap();
    assert_eq!(rebuilt.pending_operation_count().unwrap(), 0);
    assert!(rebuilt.snapshot().providers.is_empty());
    assert!(!secrets
        .values
        .lock()
        .unwrap()
        .values()
        .any(|value| value == "test-secret-must-not-be-stored"));
}
