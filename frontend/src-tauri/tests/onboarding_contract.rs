use sage_desktop_lib::onboarding::{
    CapabilityInputs, HostCapabilityProbe, LocalProviderInput, OnboardingMode, OnboardingService,
    ProviderProbe, ProviderProbeError,
};
use sage_desktop_lib::secret_broker::{SecretBroker, SecretBrokerError};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

#[derive(Default)]
struct MemorySecrets(Mutex<HashMap<String, String>>);

impl SecretBroker for MemorySecrets {
    fn store(&self, account: &str, secret: &str) -> Result<String, SecretBrokerError> {
        let key_ref = format!("memory://{account}");
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
}

impl SecretBroker for ScriptedSecrets {
    fn store(&self, account: &str, secret: &str) -> Result<String, SecretBrokerError> {
        if let Some(error) = self.failures.lock().unwrap().store.take() {
            return Err(error);
        }
        let key_ref = format!("scripted://{account}");
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

    secrets.fail_read(SecretBrokerError::Locked);
    let locked = service.probe_provider(&provider.provider_id).unwrap_err();
    assert_eq!(locked.reason_code, "keychain_locked");
    assert_eq!(service.snapshot().providers.len(), 1);

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
    assert_eq!(disconnect_denied.reason_code, "keychain_access_denied");
    assert!(service.snapshot().providers[0].key_configured);

    secrets.fail_delete(SecretBrokerError::AccessDenied);
    let delete_denied = service.delete_provider(&provider.provider_id).unwrap_err();
    assert_eq!(delete_denied.reason_code, "keychain_access_denied");
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

    service
        .rotate_provider_key(&provider.provider_id, "test-secret-new")
        .unwrap();
    assert_eq!(secrets.read(&provider.key_ref).unwrap(), "test-secret-new");
    assert_eq!(service.snapshot().providers[0].key_hint, "****-new");

    service.disconnect_provider(&provider.provider_id).unwrap();
    assert!(matches!(
        secrets.read(&provider.key_ref),
        Err(SecretBrokerError::Missing)
    ));
    assert_eq!(service.snapshot().stage, "configure_provider");

    service
        .rotate_provider_key(&provider.provider_id, "test-secret-restored")
        .unwrap();
    service.delete_provider(&provider.provider_id).unwrap();
    assert!(service.snapshot().providers.is_empty());
    assert!(matches!(
        secrets.read(&provider.key_ref),
        Err(SecretBrokerError::Missing)
    ));
}
