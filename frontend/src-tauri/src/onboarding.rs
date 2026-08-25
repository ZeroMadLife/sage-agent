use crate::secret_broker::{broker_error, SecretBroker, SecretBrokerError};
use crate::supervisor::{
    ConfigurationMutationCommitError, ConfigurationMutationGuard, ConfigurationRestartReceipt,
};
use reqwest::blocking::Client;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Manager, State};
use uuid::Uuid;
use zeroize::Zeroize;

const SCHEMA_VERSION: i64 = 2;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CapabilityInputs {
    pub docker_ready: bool,
    pub postgres_ready: bool,
    pub web_search_ready: bool,
}

pub trait HostCapabilityProbe: Send + Sync {
    fn detect(&self) -> CapabilityInputs;
}

pub trait ProviderProbe: Send + Sync {
    fn discover_models(
        &self,
        base_url: &str,
        secret: &str,
    ) -> Result<Vec<String>, ProviderProbeError>;
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProviderProbeError {
    Unavailable,
    InvalidResponse,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OnboardingMode {
    Local,
    Cloud,
}

impl OnboardingMode {
    fn as_str(self) -> &'static str {
        match self {
            Self::Local => "local",
            Self::Cloud => "cloud",
        }
    }

    fn parse(value: &str) -> Option<Self> {
        match value {
            "local" => Some(Self::Local),
            "cloud" => Some(Self::Cloud),
            _ => None,
        }
    }
}

#[derive(Deserialize)]
pub struct LocalProviderInput {
    pub name: String,
    pub base_url: String,
    pub api_key: WriteOnlySecret,
    pub default_model: String,
}

#[derive(Deserialize)]
#[serde(transparent)]
pub struct WriteOnlySecret(String);

impl WriteOnlySecret {
    fn expose(&self) -> &str {
        &self.0
    }
}

impl From<String> for WriteOnlySecret {
    fn from(value: String) -> Self {
        Self(value)
    }
}

impl From<&str> for WriteOnlySecret {
    fn from(value: &str) -> Self {
        Self(value.to_string())
    }
}

impl Drop for WriteOnlySecret {
    fn drop(&mut self) {
        self.0.zeroize();
    }
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum DesktopOnboardingAction {
    ChooseMode {
        mode: OnboardingMode,
    },
    SelectWorkspace {
        workspace_path: String,
    },
    AddProvider {
        name: String,
        base_url: String,
        api_key: WriteOnlySecret,
        default_model: String,
    },
    ProbeProvider {
        provider_id: String,
    },
    SetDefaultModel {
        provider_id: String,
        model_id: String,
    },
    SetActiveProvider {
        provider_id: String,
    },
    RotateProviderKey {
        provider_id: String,
        api_key: WriteOnlySecret,
    },
    DisconnectProvider {
        provider_id: String,
    },
    DeleteProvider {
        provider_id: String,
    },
    RetryProviderReconciliation,
}

#[derive(Clone, Debug, Serialize)]
pub struct LocalProviderView {
    pub provider_id: String,
    pub name: String,
    pub base_url: String,
    pub key_ref: String,
    pub key_hint: String,
    pub key_configured: bool,
    pub status: String,
    pub reason_code: Option<String>,
    pub models: Vec<String>,
    pub default_model: Option<String>,
    pub is_active: bool,
}

pub struct DesktopRuntimeConfiguration {
    workspace_path: PathBuf,
    provider_id: String,
    base_url: String,
    default_model: String,
    api_key: WriteOnlySecret,
    docker_ready: bool,
}

impl DesktopRuntimeConfiguration {
    pub fn workspace_path(&self) -> &Path {
        &self.workspace_path
    }

    pub fn provider_id(&self) -> &str {
        &self.provider_id
    }

    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    pub fn default_model(&self) -> &str {
        &self.default_model
    }

    pub fn api_mode(&self) -> &'static str {
        "openai_chat_completions"
    }

    pub fn sandbox_provider(&self) -> &'static str {
        if self.docker_ready {
            "container"
        } else {
            "local_workspace"
        }
    }

    pub fn side_effect_tools_enabled(&self) -> bool {
        self.docker_ready
    }

    pub(crate) fn expose_secret(&self) -> &str {
        self.api_key.expose()
    }
}

#[derive(Clone, Serialize)]
pub struct DesktopCapability {
    pub status: String,
    pub reason_code: Option<String>,
    pub action: Option<String>,
}

#[derive(Serialize)]
pub struct OnboardingSnapshot {
    pub status: String,
    pub reason_code: Option<String>,
    pub action: Option<String>,
    pub stage: String,
    pub mode: Option<OnboardingMode>,
    pub workspace_name: Option<String>,
    pub active_provider_id: Option<String>,
    pub providers: Vec<LocalProviderView>,
    pub capabilities: BTreeMap<String, DesktopCapability>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct DesktopActionError {
    pub reason_code: &'static str,
    pub action: &'static str,
}

impl DesktopActionError {
    fn new(reason_code: &'static str, action: &'static str) -> Self {
        Self {
            reason_code,
            action,
        }
    }

    fn storage() -> Self {
        Self::new("desktop_metadata_unavailable", "restart_sage")
    }

    fn reconciliation() -> Self {
        Self::new(
            "provider_reconciliation_required",
            "retry_provider_reconciliation",
        )
    }
}

impl From<SecretBrokerError> for DesktopActionError {
    fn from(error: SecretBrokerError) -> Self {
        let response = broker_error(error);
        Self::new(response.reason_code, response.action)
    }
}

struct ProviderRecord {
    provider_id: String,
    name: String,
    base_url: String,
    key_ref: String,
    key_hint: String,
    key_configured: bool,
    status: String,
    reason_code: Option<String>,
}

struct ProviderOperation {
    operation_id: String,
    kind: String,
    provider_id: String,
    phase: String,
    target_key_ref: String,
    previous_key_ref: Option<String>,
}

pub struct ProviderOperationOutcome<T> {
    pub result: Result<T, DesktopActionError>,
    pub runtime_invalidated: bool,
    restart: Option<ConfigurationRestartReceipt>,
}

struct CoordinatedMutation<T> {
    value: T,
    restart: Option<ConfigurationRestartReceipt>,
}

fn validate_configuration_guard(
    guard: Option<&ConfigurationMutationGuard>,
) -> Result<(), DesktopActionError> {
    guard
        .map(ConfigurationMutationGuard::validate)
        .transpose()
        .map(|_| ())
        .map_err(configuration_error)
}

fn run_configuration_commit<T>(
    guard: Option<&ConfigurationMutationGuard>,
    restart_required: bool,
    mutation: impl FnOnce() -> Result<T, DesktopActionError>,
) -> Result<CoordinatedMutation<T>, DesktopActionError> {
    if let Some(guard) = guard {
        let committed = guard
            .commit(restart_required, mutation)
            .map_err(|error| match error {
                ConfigurationMutationCommitError::Admission(error) => configuration_error(error),
                ConfigurationMutationCommitError::Mutation(error) => error,
            })?;
        Ok(CoordinatedMutation {
            value: committed.value,
            restart: committed.restart,
        })
    } else {
        Ok(CoordinatedMutation {
            value: mutation()?,
            restart: None,
        })
    }
}

pub struct OnboardingService {
    database_path: PathBuf,
    connection: Connection,
    secrets: Arc<dyn SecretBroker>,
    provider_probe: Arc<dyn ProviderProbe>,
    host_capabilities: Arc<dyn HostCapabilityProbe>,
}

enum OnboardingRuntime {
    Uninitialized,
    Ready(OnboardingService),
    Blocked(DesktopActionError),
}

#[derive(Clone)]
pub struct SharedOnboardingState(Arc<Mutex<OnboardingRuntime>>);

impl Default for SharedOnboardingState {
    fn default() -> Self {
        Self(Arc::new(Mutex::new(OnboardingRuntime::Uninitialized)))
    }
}

#[cfg(test)]
impl SharedOnboardingState {
    fn from_service(service: OnboardingService) -> Self {
        Self(Arc::new(Mutex::new(OnboardingRuntime::Ready(service))))
    }
}

pub fn initialize(app: AppHandle) {
    let state = app.state::<SharedOnboardingState>().inner().clone();
    let runtime = match app.path().app_data_dir() {
        Ok(data_dir) => {
            #[cfg(target_os = "macos")]
            {
                OnboardingService::open_production(data_dir)
                    .map(OnboardingRuntime::Ready)
                    .unwrap_or_else(OnboardingRuntime::Blocked)
            }
            #[cfg(not(target_os = "macos"))]
            {
                let _ = data_dir;
                OnboardingRuntime::Blocked(DesktopActionError::new(
                    "keychain_platform_unsupported",
                    "use_macos_desktop",
                ))
            }
        }
        Err(_) => OnboardingRuntime::Blocked(DesktopActionError::new(
            "desktop_data_dir_unavailable",
            "restart_sage",
        )),
    };
    *state.0.lock().expect("onboarding state poisoned") = runtime;
}

#[tauri::command]
pub fn desktop_onboarding_status(
    state: State<'_, SharedOnboardingState>,
) -> Result<OnboardingSnapshot, DesktopActionError> {
    let runtime = state.0.lock().map_err(|_| DesktopActionError::storage())?;
    match &*runtime {
        OnboardingRuntime::Ready(service) => Ok(service.snapshot()),
        OnboardingRuntime::Blocked(error) => Err(error.clone()),
        OnboardingRuntime::Uninitialized => Err(DesktopActionError::new(
            "desktop_onboarding_starting",
            "wait_for_startup",
        )),
    }
}

#[tauri::command]
pub fn desktop_onboarding_action(
    action: DesktopOnboardingAction,
    app: AppHandle,
    state: State<'_, SharedOnboardingState>,
    host: State<'_, crate::supervisor::SharedHostState>,
) -> Result<OnboardingSnapshot, DesktopActionError> {
    let mut outcome = execute_onboarding_action(action, state.inner(), host.inner());
    if let Some(receipt) = outcome.restart.take() {
        if let Err(error) =
            crate::supervisor::restart_for_configuration(app, host.inner().clone(), receipt)
        {
            if outcome.result.is_ok() {
                outcome.result = Err(configuration_error(error));
            }
        }
    }
    outcome.result
}

struct OnboardingActionOutcome {
    result: Result<OnboardingSnapshot, DesktopActionError>,
    restart: Option<ConfigurationRestartReceipt>,
}

fn execute_onboarding_action(
    action: DesktopOnboardingAction,
    state: &SharedOnboardingState,
    host: &crate::supervisor::SharedHostState,
) -> OnboardingActionOutcome {
    execute_onboarding_action_with_hook(action, state, host, || {})
}

fn execute_onboarding_action_with_hook<F>(
    action: DesktopOnboardingAction,
    state: &SharedOnboardingState,
    host: &crate::supervisor::SharedHostState,
    after_admission: F,
) -> OnboardingActionOutcome
where
    F: FnOnce(),
{
    let guard = match crate::supervisor::acquire_configuration_mutation_guard(host) {
        Ok(guard) => guard,
        Err(error) => {
            return OnboardingActionOutcome {
                result: Err(configuration_error(error)),
                restart: None,
            }
        }
    };
    after_admission();
    let mut runtime = match state.0.lock() {
        Ok(runtime) => runtime,
        Err(_) => {
            return OnboardingActionOutcome {
                result: Err(DesktopActionError::storage()),
                restart: None,
            }
        }
    };
    if let Err(error) = guard.validate() {
        return OnboardingActionOutcome {
            result: Err(configuration_error(error)),
            restart: None,
        };
    }
    let OnboardingRuntime::Ready(service) = &mut *runtime else {
        let error = match &*runtime {
            OnboardingRuntime::Blocked(error) => Err(error.clone()),
            OnboardingRuntime::Uninitialized => Err(DesktopActionError::new(
                "desktop_onboarding_starting",
                "wait_for_startup",
            )),
            OnboardingRuntime::Ready(_) => unreachable!(),
        };
        return OnboardingActionOutcome {
            result: error,
            restart: None,
        };
    };
    let applied = match apply_action(service, action, &guard) {
        Ok(value) => value,
        Err(failure) => {
            return OnboardingActionOutcome {
                result: Err(failure.error),
                restart: failure.restart,
            };
        }
    };
    let snapshot = service.snapshot();
    OnboardingActionOutcome {
        result: Ok(snapshot),
        restart: applied.restart,
    }
}

fn apply_action(
    service: &mut OnboardingService,
    action: DesktopOnboardingAction,
    guard: &ConfigurationMutationGuard,
) -> Result<ActionApplied, ActionFailure> {
    match action {
        DesktopOnboardingAction::ChooseMode { mode } => {
            commit_configuration(guard, false, || service.choose_mode(mode))
        }
        DesktopOnboardingAction::SelectWorkspace { workspace_path } => {
            commit_configuration(guard, false, || {
                service.select_workspace(Path::new(&workspace_path))
            })
        }
        DesktopOnboardingAction::AddProvider {
            name,
            base_url,
            api_key,
            default_model,
        } => {
            let mutation = service.add_provider_coordinated(
                LocalProviderInput {
                    name,
                    base_url,
                    api_key,
                    default_model,
                },
                Some(guard),
            )?;
            Ok(ActionApplied {
                restart: mutation.restart,
            })
        }
        DesktopOnboardingAction::ProbeProvider { provider_id } => {
            let mutation = service.probe_provider_coordinated(&provider_id, Some(guard))?;
            Ok(ActionApplied {
                restart: mutation.restart,
            })
        }
        DesktopOnboardingAction::SetDefaultModel {
            provider_id,
            model_id,
        } => {
            let mutation =
                service.set_default_model_coordinated(&provider_id, &model_id, Some(guard))?;
            Ok(ActionApplied {
                restart: mutation.restart,
            })
        }
        DesktopOnboardingAction::SetActiveProvider { provider_id } => {
            let mutation = service.set_active_provider_coordinated(&provider_id, Some(guard))?;
            Ok(ActionApplied {
                restart: mutation.restart,
            })
        }
        DesktopOnboardingAction::RotateProviderKey {
            provider_id,
            api_key,
        } => operation_restart(service.rotate_provider_key_operation_coordinated(
            &provider_id,
            api_key.expose(),
            Some(guard),
        )),
        DesktopOnboardingAction::DisconnectProvider { provider_id } => operation_restart(
            service.disconnect_provider_operation_coordinated(&provider_id, Some(guard)),
        ),
        DesktopOnboardingAction::DeleteProvider { provider_id } => operation_restart(
            service.delete_provider_operation_coordinated(&provider_id, Some(guard)),
        ),
        DesktopOnboardingAction::RetryProviderReconciliation => {
            service.reconcile_pending_operations()?;
            commit_configuration(guard, true, || Ok(()))
        }
    }
}

struct ActionFailure {
    error: DesktopActionError,
    restart: Option<ConfigurationRestartReceipt>,
}

struct ActionApplied {
    restart: Option<ConfigurationRestartReceipt>,
}

impl From<DesktopActionError> for ActionFailure {
    fn from(error: DesktopActionError) -> Self {
        Self {
            error,
            restart: None,
        }
    }
}

fn operation_restart<T>(
    outcome: ProviderOperationOutcome<T>,
) -> Result<ActionApplied, ActionFailure> {
    match outcome.result {
        Ok(_) => Ok(ActionApplied {
            restart: outcome.restart,
        }),
        Err(error) => Err(ActionFailure {
            error,
            restart: outcome.restart,
        }),
    }
}

fn configuration_error(error: crate::supervisor::ConfigurationActionFailure) -> DesktopActionError {
    DesktopActionError::new(error.reason_code, error.action)
}

fn commit_configuration<T>(
    guard: &ConfigurationMutationGuard,
    restart_required: bool,
    mutation: impl FnOnce() -> Result<T, DesktopActionError>,
) -> Result<ActionApplied, ActionFailure> {
    let committed = guard
        .commit(restart_required, mutation)
        .map_err(|error| match error {
            ConfigurationMutationCommitError::Admission(error) => {
                ActionFailure::from(configuration_error(error))
            }
            ConfigurationMutationCommitError::Mutation(error) => ActionFailure::from(error),
        })?;
    Ok(ActionApplied {
        restart: committed.restart,
    })
}

pub fn runtime_configuration_for_app(
    app: &AppHandle,
) -> Result<Option<DesktopRuntimeConfiguration>, DesktopActionError> {
    let state = app.state::<SharedOnboardingState>();
    let runtime = state.0.lock().map_err(|_| DesktopActionError::storage())?;
    match &*runtime {
        OnboardingRuntime::Ready(service) => service.runtime_configuration(),
        OnboardingRuntime::Blocked(error) => Err(error.clone()),
        OnboardingRuntime::Uninitialized => Ok(None),
    }
}

impl OnboardingService {
    pub fn open_with(
        data_dir: PathBuf,
        secrets: Arc<dyn SecretBroker>,
        provider_probe: Arc<dyn ProviderProbe>,
        host_capabilities: Arc<dyn HostCapabilityProbe>,
    ) -> Result<Self, DesktopActionError> {
        std::fs::create_dir_all(&data_dir).map_err(|_| DesktopActionError::storage())?;
        let database_path = data_dir.join("desktop-onboarding.sqlite3");
        let connection =
            Connection::open(&database_path).map_err(|_| DesktopActionError::storage())?;
        connection
            .pragma_update(None, "foreign_keys", "ON")
            .map_err(|_| DesktopActionError::storage())?;
        migrate(&connection)?;
        let mut service = Self {
            database_path,
            connection,
            secrets,
            provider_probe,
            host_capabilities,
        };
        let _ = service.reconcile_pending_operations();
        Ok(service)
    }

    #[cfg(target_os = "macos")]
    pub fn open_production(data_dir: PathBuf) -> Result<Self, DesktopActionError> {
        Self::open_with(
            data_dir,
            Arc::new(crate::secret_broker::MacOsKeychain::sage_local_provider()),
            Arc::new(HttpProviderProbe),
            Arc::new(SystemHostCapabilityProbe),
        )
    }

    pub fn database_path(&self) -> &Path {
        &self.database_path
    }

    pub fn choose_mode(&mut self, mode: OnboardingMode) -> Result<(), DesktopActionError> {
        self.connection
            .execute(
                "INSERT INTO desktop_onboarding (singleton, mode) VALUES (1, ?1) \
                 ON CONFLICT(singleton) DO UPDATE SET mode = excluded.mode",
                [mode.as_str()],
            )
            .map_err(|_| DesktopActionError::storage())?;
        Ok(())
    }

    pub fn select_workspace(&mut self, workspace: &Path) -> Result<(), DesktopActionError> {
        let canonical = workspace
            .canonicalize()
            .map_err(|_| DesktopActionError::new("workspace_unavailable", "select_workspace"))?;
        if !canonical.is_dir() {
            return Err(DesktopActionError::new(
                "workspace_not_directory",
                "select_workspace",
            ));
        }
        self.connection
            .execute(
                "INSERT INTO desktop_onboarding (singleton, workspace_path) VALUES (1, ?1) \
                 ON CONFLICT(singleton) DO UPDATE SET workspace_path = excluded.workspace_path",
                [canonical.to_string_lossy().as_ref()],
            )
            .map_err(|_| DesktopActionError::storage())?;
        Ok(())
    }

    pub fn add_provider(
        &mut self,
        input: LocalProviderInput,
    ) -> Result<LocalProviderView, DesktopActionError> {
        self.add_provider_coordinated(input, None)
            .map(|mutation| mutation.value)
    }

    fn add_provider_coordinated(
        &mut self,
        input: LocalProviderInput,
        guard: Option<&ConfigurationMutationGuard>,
    ) -> Result<CoordinatedMutation<LocalProviderView>, DesktopActionError> {
        validate_configuration_guard(guard)?;
        self.ensure_provider_operations_settled()?;
        let name = normalized_label(&input.name, "provider_name_invalid")?;
        let base_url = normalized_base_url(&input.base_url)?;
        let default_model = normalized_label(&input.default_model, "provider_model_invalid")?;
        if input.api_key.expose().trim().is_empty() {
            return Err(DesktopActionError::new(
                "provider_key_invalid",
                "reenter_provider_key",
            ));
        }
        let provider_id = Uuid::new_v4().to_string();
        let operation_id = Uuid::new_v4().to_string();
        let account = format!("{provider_id}-{operation_id}");
        let key_ref = self.secrets.key_ref(&account)?;
        let key_hint = key_hint(input.api_key.expose().trim());
        self.connection
            .execute(
                "INSERT INTO provider_operations
                 (operation_id, kind, provider_id, phase, target_key_ref,
                  provider_name, base_url, default_model, key_hint)
                 VALUES (?1, 'add', ?2, 'planned', ?3, ?4, ?5, ?6, ?7)",
                params![
                    operation_id,
                    provider_id,
                    key_ref,
                    name,
                    base_url,
                    default_model,
                    key_hint
                ],
            )
            .map_err(|_| DesktopActionError::storage())?;
        let stored_ref = match self.secrets.store(&account, input.api_key.expose().trim()) {
            Ok(value) => value,
            Err(error) => {
                let _ = self.remove_operation(&operation_id);
                return Err(error.into());
            }
        };
        if stored_ref != key_ref {
            let _ = self.secrets.delete(&stored_ref);
            self.record_operation_error(&operation_id, "key_ref_mismatch");
            return Err(DesktopActionError::reconciliation());
        }
        if self
            .connection
            .execute(
                "UPDATE provider_operations SET phase = 'secret_applied' WHERE operation_id = ?1",
                [&operation_id],
            )
            .is_err()
        {
            return self.compensate_new_secret(&operation_id, &key_ref);
        }
        let committed = run_configuration_commit(guard, false, || {
            let transaction = self
                .connection
                .transaction()
                .map_err(|_| DesktopActionError::reconciliation())?;
            transaction
                .execute(
                    "INSERT INTO local_providers \
                     (provider_id, name, base_url, key_ref, key_hint, key_configured, status) \
                     VALUES (?1, ?2, ?3, ?4, ?5, 1, 'untested')",
                    params![provider_id, name, base_url, key_ref, key_hint],
                )
                .and_then(|_| {
                    transaction.execute(
                        "INSERT INTO local_provider_models (provider_id, model_id, is_default) \
                         VALUES (?1, ?2, 1)",
                        params![provider_id, default_model],
                    )
                })
                .and_then(|_| {
                    transaction.execute(
                        "DELETE FROM provider_operations WHERE operation_id = ?1",
                        [&operation_id],
                    )
                })
                .and_then(|_| transaction.commit())
                .map_err(|_| DesktopActionError::reconciliation())?;
            Ok(())
        });
        let restart = match committed {
            Ok(committed) => committed.restart,
            Err(error) => return self.compensate_new_secret_after(&operation_id, &key_ref, error),
        };
        Ok(CoordinatedMutation {
            value: self.provider_view(&provider_id)?,
            restart,
        })
    }

    pub fn probe_provider(
        &mut self,
        provider_id: &str,
    ) -> Result<LocalProviderView, DesktopActionError> {
        self.probe_provider_coordinated(provider_id, None)
            .map(|mutation| mutation.value)
    }

    fn probe_provider_coordinated(
        &mut self,
        provider_id: &str,
        guard: Option<&ConfigurationMutationGuard>,
    ) -> Result<CoordinatedMutation<LocalProviderView>, DesktopActionError> {
        validate_configuration_guard(guard)?;
        self.ensure_provider_operations_settled()?;
        let provider = self.provider_record(provider_id)?;
        if !provider.key_configured {
            return Err(SecretBrokerError::Missing.into());
        }
        let mut secret = self.secrets.read(&provider.key_ref)?;
        let probe_result = self
            .provider_probe
            .discover_models(&provider.base_url, &secret);
        secret.zeroize();
        let models = match probe_result {
            Ok(models) if !models.is_empty() => models,
            Ok(_) | Err(ProviderProbeError::InvalidResponse) => {
                if guard.is_none() {
                    self.record_probe_failure(provider_id, "provider_probe_invalid_response")?;
                }
                return Err(DesktopActionError::new(
                    "provider_probe_invalid_response",
                    "check_provider_settings",
                ));
            }
            Err(ProviderProbeError::Unavailable) => {
                if guard.is_none() {
                    self.record_probe_failure(provider_id, "provider_probe_failed")?;
                }
                return Err(DesktopActionError::new(
                    "provider_probe_failed",
                    "check_provider_settings",
                ));
            }
        };
        let models = normalize_models(models)?;
        let current_default = self.default_model(provider_id)?;
        let selected_default = current_default
            .filter(|value| models.contains(value))
            .unwrap_or_else(|| models[0].clone());
        let restart_required =
            self.is_active_provider(provider_id)? || self.active_provider_id()?.is_none();
        let committed =
            run_configuration_commit(guard, restart_required, || {
                let transaction = self
                    .connection
                    .transaction()
                    .map_err(|_| DesktopActionError::storage())?;
                transaction
                    .execute(
                        "DELETE FROM local_provider_models WHERE provider_id = ?1",
                        [provider_id],
                    )
                    .map_err(|_| DesktopActionError::storage())?;
                for model in &models {
                    transaction.execute(
                    "INSERT INTO local_provider_models (provider_id, model_id, is_default) \
                     VALUES (?1, ?2, ?3)",
                    params![provider_id, model, i64::from(model == &selected_default)],
                ).map_err(|_| DesktopActionError::storage())?;
                }
                transaction
                    .execute(
                        "UPDATE local_providers SET status = 'connected', reason_code = NULL \
                 WHERE provider_id = ?1",
                        [provider_id],
                    )
                    .and_then(|_| {
                        transaction.execute(
                            "UPDATE desktop_onboarding SET active_provider_id = ?1
                     WHERE singleton = 1 AND active_provider_id IS NULL",
                            [provider_id],
                        )
                    })
                    .and_then(|_| transaction.commit())
                    .map_err(|_| DesktopActionError::storage())?;
                Ok(())
            })?;
        Ok(CoordinatedMutation {
            value: self.provider_view(provider_id)?,
            restart: committed.restart,
        })
    }

    pub fn set_active_provider(
        &mut self,
        provider_id: &str,
    ) -> Result<LocalProviderView, DesktopActionError> {
        self.set_active_provider_coordinated(provider_id, None)
            .map(|mutation| mutation.value)
    }

    fn set_active_provider_coordinated(
        &mut self,
        provider_id: &str,
        guard: Option<&ConfigurationMutationGuard>,
    ) -> Result<CoordinatedMutation<LocalProviderView>, DesktopActionError> {
        validate_configuration_guard(guard)?;
        self.ensure_provider_operations_settled()?;
        let provider = self.provider_record(provider_id)?;
        if provider.status != "connected" || !provider.key_configured {
            return Err(DesktopActionError::new(
                "provider_not_connected",
                "probe_provider",
            ));
        }
        let mut secret = self.secrets.read(&provider.key_ref)?;
        secret.zeroize();
        let committed = run_configuration_commit(guard, true, || {
            self.connection
                .execute(
                    "INSERT INTO desktop_onboarding (singleton, active_provider_id)
                 VALUES (1, ?1) ON CONFLICT(singleton) DO UPDATE
                 SET active_provider_id = excluded.active_provider_id",
                    [provider_id],
                )
                .map_err(|_| DesktopActionError::storage())?;
            Ok(())
        })?;
        Ok(CoordinatedMutation {
            value: self.provider_view(provider_id)?,
            restart: committed.restart,
        })
    }

    pub fn set_default_model(
        &mut self,
        provider_id: &str,
        model_id: &str,
    ) -> Result<LocalProviderView, DesktopActionError> {
        self.set_default_model_coordinated(provider_id, model_id, None)
            .map(|mutation| mutation.value)
    }

    fn set_default_model_coordinated(
        &mut self,
        provider_id: &str,
        model_id: &str,
        guard: Option<&ConfigurationMutationGuard>,
    ) -> Result<CoordinatedMutation<LocalProviderView>, DesktopActionError> {
        validate_configuration_guard(guard)?;
        self.ensure_provider_operations_settled()?;
        let model_id = normalized_label(model_id, "provider_model_invalid")?;
        let exists: bool = self
            .connection
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM local_provider_models \
                 WHERE provider_id = ?1 AND model_id = ?2)",
                params![provider_id, model_id],
                |row| row.get(0),
            )
            .map_err(|_| DesktopActionError::storage())?;
        if !exists {
            return Err(DesktopActionError::new(
                "provider_model_unknown",
                "select_provider_model",
            ));
        }
        let restart_required = self.is_active_provider(provider_id)?;
        let committed = run_configuration_commit(guard, restart_required, || {
            let transaction = self
                .connection
                .transaction()
                .map_err(|_| DesktopActionError::storage())?;
            transaction
                .execute(
                    "UPDATE local_provider_models SET is_default = 0 WHERE provider_id = ?1",
                    [provider_id],
                )
                .and_then(|_| {
                    transaction.execute(
                        "UPDATE local_provider_models SET is_default = 1 \
                     WHERE provider_id = ?1 AND model_id = ?2",
                        params![provider_id, model_id],
                    )
                })
                .and_then(|_| transaction.commit())
                .map_err(|_| DesktopActionError::storage())?;
            Ok(())
        })?;
        Ok(CoordinatedMutation {
            value: self.provider_view(provider_id)?,
            restart: committed.restart,
        })
    }

    pub fn rotate_provider_key(
        &mut self,
        provider_id: &str,
        new_secret: &str,
    ) -> Result<LocalProviderView, DesktopActionError> {
        self.rotate_provider_key_operation(provider_id, new_secret)
            .result
    }

    pub fn rotate_provider_key_operation(
        &mut self,
        provider_id: &str,
        new_secret: &str,
    ) -> ProviderOperationOutcome<LocalProviderView> {
        self.rotate_provider_key_operation_coordinated(provider_id, new_secret, None)
    }

    fn rotate_provider_key_operation_coordinated(
        &mut self,
        provider_id: &str,
        new_secret: &str,
        guard: Option<&ConfigurationMutationGuard>,
    ) -> ProviderOperationOutcome<LocalProviderView> {
        let was_active = match self.is_active_provider(provider_id) {
            Ok(value) => value,
            Err(error) => {
                return ProviderOperationOutcome {
                    result: Err(error),
                    runtime_invalidated: false,
                    restart: None,
                };
            }
        };
        let mut restart = None;
        let result = self.rotate_provider_key_inner(
            provider_id,
            new_secret,
            guard,
            was_active,
            &mut restart,
        );
        ProviderOperationOutcome {
            runtime_invalidated: was_active
                && (result.is_ok() || self.provider_operation_pending(provider_id)),
            result,
            restart,
        }
    }

    fn rotate_provider_key_inner(
        &mut self,
        provider_id: &str,
        new_secret: &str,
        guard: Option<&ConfigurationMutationGuard>,
        restart_required: bool,
        restart: &mut Option<ConfigurationRestartReceipt>,
    ) -> Result<LocalProviderView, DesktopActionError> {
        validate_configuration_guard(guard)?;
        self.ensure_provider_operations_settled()?;
        if new_secret.trim().is_empty() {
            return Err(DesktopActionError::new(
                "provider_key_invalid",
                "reenter_provider_key",
            ));
        }
        let provider = self.provider_record(provider_id)?;
        let operation_id = Uuid::new_v4().to_string();
        let account = format!("{provider_id}-{operation_id}");
        let key_ref = self.secrets.key_ref(&account)?;
        self.insert_operation(
            &operation_id,
            "rotate",
            provider_id,
            &key_ref,
            Some(&provider.key_ref),
        )?;
        let stored_ref = match self.secrets.store(&account, new_secret.trim()) {
            Ok(value) => value,
            Err(error) => {
                let _ = self.remove_operation(&operation_id);
                return Err(error.into());
            }
        };
        if stored_ref != key_ref {
            let _ = self.secrets.delete(&stored_ref);
            self.record_operation_error(&operation_id, "key_ref_mismatch");
            return Err(DesktopActionError::reconciliation());
        }
        if self
            .connection
            .execute(
                "UPDATE provider_operations SET phase = 'secret_applied' WHERE operation_id = ?1",
                [&operation_id],
            )
            .is_err()
        {
            return self.compensate_new_secret(&operation_id, &key_ref);
        }
        let committed = run_configuration_commit(guard, restart_required, || {
            let transaction = self
                .connection
                .transaction()
                .map_err(|_| DesktopActionError::reconciliation())?;
            transaction
                .execute(
                    "UPDATE local_providers SET key_ref = ?2, key_hint = ?3, key_configured = 1,
                 status = 'untested', reason_code = NULL WHERE provider_id = ?1",
                    params![provider_id, key_ref, key_hint(new_secret.trim())],
                )
                .and_then(|_| {
                    transaction.execute(
                        "UPDATE provider_operations SET phase = 'metadata_applied'
                 WHERE operation_id = ?1",
                        [&operation_id],
                    )
                })
                .and_then(|_| transaction.commit())
                .map_err(|_| DesktopActionError::reconciliation())?;
            Ok(())
        });
        *restart = match committed {
            Ok(committed) => committed.restart,
            Err(error) => return self.compensate_new_secret_after(&operation_id, &key_ref, error),
        };
        if provider.key_configured {
            if let Err(error) = self.delete_secret_if_present(&provider.key_ref) {
                self.record_operation_error(&operation_id, error.reason_code);
                return Err(DesktopActionError::reconciliation());
            }
        }
        self.remove_operation(&operation_id)?;
        self.provider_view(provider_id)
    }

    pub fn disconnect_provider(
        &mut self,
        provider_id: &str,
    ) -> Result<LocalProviderView, DesktopActionError> {
        self.disconnect_provider_operation(provider_id).result
    }

    pub fn disconnect_provider_operation(
        &mut self,
        provider_id: &str,
    ) -> ProviderOperationOutcome<LocalProviderView> {
        self.disconnect_provider_operation_coordinated(provider_id, None)
    }

    fn disconnect_provider_operation_coordinated(
        &mut self,
        provider_id: &str,
        guard: Option<&ConfigurationMutationGuard>,
    ) -> ProviderOperationOutcome<LocalProviderView> {
        let was_active = match self.is_active_provider(provider_id) {
            Ok(value) => value,
            Err(error) => {
                return ProviderOperationOutcome {
                    result: Err(error),
                    runtime_invalidated: false,
                    restart: None,
                };
            }
        };
        let mut restart = None;
        let result = self.disconnect_provider_inner(provider_id, guard, was_active, &mut restart);
        ProviderOperationOutcome {
            runtime_invalidated: was_active
                && (result.is_ok() || self.provider_operation_pending(provider_id)),
            result,
            restart,
        }
    }

    fn disconnect_provider_inner(
        &mut self,
        provider_id: &str,
        guard: Option<&ConfigurationMutationGuard>,
        restart_required: bool,
        restart: &mut Option<ConfigurationRestartReceipt>,
    ) -> Result<LocalProviderView, DesktopActionError> {
        validate_configuration_guard(guard)?;
        self.ensure_provider_operations_settled()?;
        let provider = self.provider_record(provider_id)?;
        let operation_id = Uuid::new_v4().to_string();
        self.insert_operation(
            &operation_id,
            "disconnect",
            provider_id,
            &provider.key_ref,
            None,
        )?;
        let committed = run_configuration_commit(guard, restart_required, || {
            let transaction = self
                .connection
                .transaction()
                .map_err(|_| DesktopActionError::storage())?;
            transaction
                .execute(
                    "UPDATE local_providers SET key_hint = '', key_configured = 0,
             status = 'disconnected', reason_code = 'provider_key_disconnected'
             WHERE provider_id = ?1",
                    [provider_id],
                )
                .and_then(|_| {
                    transaction.execute(
                        "UPDATE desktop_onboarding SET active_provider_id = NULL
             WHERE singleton = 1 AND active_provider_id = ?1",
                        [provider_id],
                    )
                })
                .and_then(|_| {
                    transaction.execute(
            "UPDATE provider_operations SET phase = 'metadata_applied' WHERE operation_id = ?1",
            [&operation_id],
        )
                })
                .and_then(|_| transaction.commit())
                .map_err(|_| DesktopActionError::storage())?;
            Ok(())
        });
        *restart = match committed {
            Ok(committed) => committed.restart,
            Err(error) => {
                if is_configuration_admission_error(&error) {
                    self.remove_operation(&operation_id)?;
                }
                return Err(error);
            }
        };
        if let Err(error) = self.delete_secret_if_present(&provider.key_ref) {
            self.record_operation_error(&operation_id, error.reason_code);
            return Err(DesktopActionError::reconciliation());
        }
        self.remove_operation(&operation_id)?;
        self.provider_view(provider_id)
    }

    pub fn delete_provider(&mut self, provider_id: &str) -> Result<(), DesktopActionError> {
        self.delete_provider_operation(provider_id).result
    }

    pub fn delete_provider_operation(&mut self, provider_id: &str) -> ProviderOperationOutcome<()> {
        self.delete_provider_operation_coordinated(provider_id, None)
    }

    fn delete_provider_operation_coordinated(
        &mut self,
        provider_id: &str,
        guard: Option<&ConfigurationMutationGuard>,
    ) -> ProviderOperationOutcome<()> {
        let was_active = match self.is_active_provider(provider_id) {
            Ok(value) => value,
            Err(error) => {
                return ProviderOperationOutcome {
                    result: Err(error),
                    runtime_invalidated: false,
                    restart: None,
                };
            }
        };
        let mut restart = None;
        let result = self.delete_provider_inner(provider_id, guard, was_active, &mut restart);
        ProviderOperationOutcome {
            runtime_invalidated: was_active
                && (result.is_ok() || self.provider_operation_pending(provider_id)),
            result,
            restart,
        }
    }

    fn delete_provider_inner(
        &mut self,
        provider_id: &str,
        guard: Option<&ConfigurationMutationGuard>,
        restart_required: bool,
        restart: &mut Option<ConfigurationRestartReceipt>,
    ) -> Result<(), DesktopActionError> {
        validate_configuration_guard(guard)?;
        self.ensure_provider_operations_settled()?;
        let provider = self.provider_record(provider_id)?;
        let operation_id = Uuid::new_v4().to_string();
        self.insert_operation(
            &operation_id,
            "delete",
            provider_id,
            &provider.key_ref,
            None,
        )?;
        let committed = run_configuration_commit(guard, restart_required, || {
            let transaction = self
                .connection
                .transaction()
                .map_err(|_| DesktopActionError::storage())?;
            transaction
                .execute(
                    "UPDATE local_providers SET key_configured = 0, status = 'pending_delete',
             reason_code = 'provider_delete_pending' WHERE provider_id = ?1",
                    [provider_id],
                )
                .and_then(|_| {
                    transaction.execute(
                        "UPDATE desktop_onboarding SET active_provider_id = NULL
             WHERE singleton = 1 AND active_provider_id = ?1",
                        [provider_id],
                    )
                })
                .and_then(|_| {
                    transaction.execute(
            "UPDATE provider_operations SET phase = 'metadata_applied' WHERE operation_id = ?1",
            [&operation_id],
        )
                })
                .and_then(|_| transaction.commit())
                .map_err(|_| DesktopActionError::storage())?;
            Ok(())
        });
        *restart = match committed {
            Ok(committed) => committed.restart,
            Err(error) => {
                if is_configuration_admission_error(&error) {
                    self.remove_operation(&operation_id)?;
                }
                return Err(error);
            }
        };
        if let Err(error) = self.delete_secret_if_present(&provider.key_ref) {
            self.record_operation_error(&operation_id, error.reason_code);
            return Err(DesktopActionError::reconciliation());
        }
        let transaction = self
            .connection
            .transaction()
            .map_err(|_| DesktopActionError::storage())?;
        transaction
            .execute(
                "DELETE FROM local_providers WHERE provider_id = ?1",
                [provider_id],
            )
            .and_then(|_| {
                transaction.execute(
                    "DELETE FROM provider_operations WHERE operation_id = ?1",
                    [&operation_id],
                )
            })
            .and_then(|_| transaction.commit())
            .map_err(|_| DesktopActionError::reconciliation())?;
        Ok(())
    }

    fn provider_operation_pending(&self, provider_id: &str) -> bool {
        self.connection
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM provider_operations WHERE provider_id = ?1)",
                [provider_id],
                |row| row.get::<_, bool>(0),
            )
            .unwrap_or(true)
    }

    fn insert_operation(
        &self,
        operation_id: &str,
        kind: &str,
        provider_id: &str,
        target_key_ref: &str,
        previous_key_ref: Option<&str>,
    ) -> Result<(), DesktopActionError> {
        self.connection
            .execute(
                "INSERT INTO provider_operations
             (operation_id, kind, provider_id, phase, target_key_ref, previous_key_ref)
             VALUES (?1, ?2, ?3, 'planned', ?4, ?5)",
                params![
                    operation_id,
                    kind,
                    provider_id,
                    target_key_ref,
                    previous_key_ref
                ],
            )
            .map_err(|_| DesktopActionError::storage())?;
        Ok(())
    }

    fn remove_operation(&self, operation_id: &str) -> Result<(), DesktopActionError> {
        self.connection
            .execute(
                "DELETE FROM provider_operations WHERE operation_id = ?1",
                [operation_id],
            )
            .map_err(|_| DesktopActionError::reconciliation())?;
        Ok(())
    }

    fn record_operation_error(&self, operation_id: &str, reason_code: &str) {
        let _ = self.connection.execute(
            "UPDATE provider_operations SET last_error = ?2 WHERE operation_id = ?1",
            params![operation_id, reason_code],
        );
    }

    fn delete_secret_if_present(&self, key_ref: &str) -> Result<(), DesktopActionError> {
        match self.secrets.delete(key_ref) {
            Ok(()) | Err(SecretBrokerError::Missing) => Ok(()),
            Err(error) => Err(error.into()),
        }
    }

    fn compensate_new_secret<T>(
        &self,
        operation_id: &str,
        key_ref: &str,
    ) -> Result<T, DesktopActionError> {
        match self.delete_secret_if_present(key_ref) {
            Ok(()) => {
                let _ = self.remove_operation(operation_id);
                Err(DesktopActionError::storage())
            }
            Err(error) => {
                self.record_operation_error(operation_id, error.reason_code);
                Err(DesktopActionError::reconciliation())
            }
        }
    }

    fn compensate_new_secret_after<T>(
        &self,
        operation_id: &str,
        key_ref: &str,
        original: DesktopActionError,
    ) -> Result<T, DesktopActionError> {
        match self.delete_secret_if_present(key_ref) {
            Ok(()) => {
                self.remove_operation(operation_id)?;
                Err(original)
            }
            Err(error) => {
                self.record_operation_error(operation_id, error.reason_code);
                Err(DesktopActionError::reconciliation())
            }
        }
    }

    fn pending_operations(&self) -> Result<Vec<ProviderOperation>, DesktopActionError> {
        let mut statement = self
            .connection
            .prepare(
                "SELECT operation_id, kind, provider_id, phase, target_key_ref, previous_key_ref
             FROM provider_operations ORDER BY rowid",
            )
            .map_err(|_| DesktopActionError::storage())?;
        let rows = statement
            .query_map([], |row| {
                Ok(ProviderOperation {
                    operation_id: row.get(0)?,
                    kind: row.get(1)?,
                    provider_id: row.get(2)?,
                    phase: row.get(3)?,
                    target_key_ref: row.get(4)?,
                    previous_key_ref: row.get(5)?,
                })
            })
            .map_err(|_| DesktopActionError::storage())?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| DesktopActionError::storage())?;
        Ok(rows)
    }

    pub fn pending_operation_count(&self) -> Result<usize, DesktopActionError> {
        Ok(self.pending_operations()?.len())
    }

    pub fn pending_operation_key_refs(&self) -> Result<Vec<String>, DesktopActionError> {
        Ok(self
            .pending_operations()?
            .into_iter()
            .map(|item| item.target_key_ref)
            .collect())
    }

    fn ensure_provider_operations_settled(&self) -> Result<(), DesktopActionError> {
        if self.pending_operation_count()? > 0 {
            return Err(DesktopActionError::reconciliation());
        }
        Ok(())
    }

    pub fn reconcile_pending_operations(&mut self) -> Result<(), DesktopActionError> {
        for operation in self.pending_operations()? {
            let metadata_ref = self
                .connection
                .query_row(
                    "SELECT key_ref FROM local_providers WHERE provider_id = ?1",
                    [&operation.provider_id],
                    |row| row.get::<_, String>(0),
                )
                .optional()
                .map_err(|_| DesktopActionError::storage())?;
            match operation.kind.as_str() {
                "add" => {
                    if metadata_ref.as_deref() == Some(operation.target_key_ref.as_str()) {
                        self.remove_operation(&operation.operation_id)?;
                    } else {
                        self.delete_secret_if_present(&operation.target_key_ref)
                            .map_err(|error| {
                                self.record_operation_error(
                                    &operation.operation_id,
                                    error.reason_code,
                                );
                                DesktopActionError::reconciliation()
                            })?;
                        self.remove_operation(&operation.operation_id)?;
                    }
                }
                "rotate" => {
                    if metadata_ref.as_deref() == Some(operation.target_key_ref.as_str())
                        || operation.phase == "metadata_applied"
                    {
                        if let Some(previous) = operation.previous_key_ref.as_deref() {
                            self.delete_secret_if_present(previous).map_err(|error| {
                                self.record_operation_error(
                                    &operation.operation_id,
                                    error.reason_code,
                                );
                                DesktopActionError::reconciliation()
                            })?;
                        }
                    } else {
                        self.delete_secret_if_present(&operation.target_key_ref)
                            .map_err(|error| {
                                self.record_operation_error(
                                    &operation.operation_id,
                                    error.reason_code,
                                );
                                DesktopActionError::reconciliation()
                            })?;
                    }
                    self.remove_operation(&operation.operation_id)?;
                }
                "disconnect" => {
                    if operation.phase != "metadata_applied" {
                        self.remove_operation(&operation.operation_id)?;
                        continue;
                    }
                    self.delete_secret_if_present(&operation.target_key_ref)
                        .map_err(|error| {
                            self.record_operation_error(&operation.operation_id, error.reason_code);
                            DesktopActionError::reconciliation()
                        })?;
                    self.remove_operation(&operation.operation_id)?;
                }
                "delete" => {
                    if operation.phase != "metadata_applied" {
                        self.remove_operation(&operation.operation_id)?;
                        continue;
                    }
                    self.delete_secret_if_present(&operation.target_key_ref)
                        .map_err(|error| {
                            self.record_operation_error(&operation.operation_id, error.reason_code);
                            DesktopActionError::reconciliation()
                        })?;
                    let transaction = self
                        .connection
                        .transaction()
                        .map_err(|_| DesktopActionError::storage())?;
                    transaction
                        .execute(
                            "DELETE FROM local_providers WHERE provider_id = ?1",
                            [&operation.provider_id],
                        )
                        .and_then(|_| {
                            transaction.execute(
                                "DELETE FROM provider_operations WHERE operation_id = ?1",
                                [&operation.operation_id],
                            )
                        })
                        .and_then(|_| transaction.commit())
                        .map_err(|_| DesktopActionError::reconciliation())?;
                }
                _ => return Err(DesktopActionError::reconciliation()),
            }
        }
        Ok(())
    }

    fn active_provider_id(&self) -> Result<Option<String>, DesktopActionError> {
        self.connection
            .query_row(
                "SELECT active_provider_id FROM desktop_onboarding WHERE singleton = 1",
                [],
                |row| row.get(0),
            )
            .optional()
            .map(|value| value.flatten())
            .map_err(|_| DesktopActionError::storage())
    }

    fn is_active_provider(&self, provider_id: &str) -> Result<bool, DesktopActionError> {
        Ok(self.active_provider_id()?.as_deref() == Some(provider_id))
    }

    pub fn snapshot(&self) -> OnboardingSnapshot {
        self.try_snapshot()
            .unwrap_or_else(|error| OnboardingSnapshot {
                status: "blocked".into(),
                reason_code: Some(error.reason_code.into()),
                action: Some(error.action.into()),
                stage: "blocked".into(),
                mode: None,
                workspace_name: None,
                active_provider_id: None,
                providers: Vec::new(),
                capabilities: BTreeMap::new(),
            })
    }

    pub fn runtime_configuration(
        &self,
    ) -> Result<Option<DesktopRuntimeConfiguration>, DesktopActionError> {
        let (mode, workspace) = self.onboarding_selection()?;
        if mode != Some(OnboardingMode::Local) {
            return Ok(None);
        }
        let Some(workspace_path) = workspace.filter(|path| path.is_dir()) else {
            return Ok(None);
        };
        if self.pending_operation_count()? > 0 {
            return Err(DesktopActionError::reconciliation());
        }
        let Some(active_provider_id) = self.active_provider_id()? else {
            return Ok(None);
        };
        let provider = self.provider_view(&active_provider_id)?;
        if provider.status != "connected" || !provider.key_configured {
            return Ok(None);
        }
        let Some(default_model) = provider.default_model else {
            return Ok(None);
        };
        let api_key = WriteOnlySecret::from(self.secrets.read(&provider.key_ref)?);
        let docker_ready = self.host_capabilities.detect().docker_ready;
        Ok(Some(DesktopRuntimeConfiguration {
            workspace_path,
            provider_id: provider.provider_id,
            base_url: provider.base_url,
            default_model,
            api_key,
            docker_ready,
        }))
    }

    fn try_snapshot(&self) -> Result<OnboardingSnapshot, DesktopActionError> {
        let (mode, workspace) = self.onboarding_selection()?;
        let providers = self.provider_views()?;
        let active_provider_id = self.active_provider_id()?;
        let workspace_ready = workspace.as_ref().is_some_and(|path| path.is_dir());
        let mut provider_secret_error = None;
        let provider_ready = providers.iter().any(|provider| {
            if active_provider_id.as_deref() != Some(provider.provider_id.as_str()) {
                return false;
            }
            if !provider.key_configured || provider.status != "connected" {
                return false;
            }
            match self.secrets.read(&provider.key_ref) {
                Ok(mut secret) => {
                    secret.zeroize();
                    true
                }
                Err(error) => {
                    if provider_secret_error.is_none() {
                        provider_secret_error = Some(DesktopActionError::from(error));
                    }
                    false
                }
            }
        });
        let stage = match mode {
            None => "choose_mode",
            Some(OnboardingMode::Cloud) => "cloud_unavailable",
            Some(OnboardingMode::Local) if !workspace_ready => "select_workspace",
            Some(OnboardingMode::Local) if !provider_ready => "configure_provider",
            Some(OnboardingMode::Local) => "complete",
        };
        let pending_operations = self.pending_operation_count()?;
        let host = self.host_capabilities.detect();
        let mut capabilities = capability_matrix(mode, workspace_ready, provider_ready, host);
        if let Some(error) = provider_secret_error.as_ref() {
            capabilities.insert(
                "provider".into(),
                capability("blocked", Some(error.reason_code), Some(error.action)),
            );
        }
        let (status, reason_code, action) = if pending_operations > 0 {
            (
                "blocked",
                Some("provider_reconciliation_required".into()),
                Some("retry_provider_reconciliation".into()),
            )
        } else if stage == "complete" {
            let degraded = capabilities
                .values()
                .any(|capability| capability.status != "ready");
            (
                if degraded { "degraded" } else { "ready" },
                degraded.then_some("optional_capabilities_unavailable".into()),
                degraded.then_some("review_capabilities".into()),
            )
        } else {
            let (reason, action) = match (stage, provider_secret_error.as_ref()) {
                ("configure_provider", Some(error)) => (error.reason_code, error.action),
                ("choose_mode", _) => ("onboarding_mode_required", "choose_onboarding_mode"),
                ("cloud_unavailable", _) => ("cloud_oauth_not_available", "choose_local_mode"),
                ("select_workspace", _) => ("workspace_required", "select_workspace"),
                _ if active_provider_id.is_none() && !providers.is_empty() => {
                    ("active_provider_required", "select_active_provider")
                }
                _ => ("provider_not_configured", "configure_provider"),
            };
            ("blocked", Some(reason.into()), Some(action.into()))
        };
        let workspace_name = workspace.and_then(|path| {
            path.file_name()
                .map(|name| name.to_string_lossy().into_owned())
        });
        Ok(OnboardingSnapshot {
            status: status.into(),
            reason_code,
            action,
            stage: stage.into(),
            mode,
            workspace_name,
            active_provider_id,
            providers,
            capabilities,
        })
    }

    fn onboarding_selection(
        &self,
    ) -> Result<(Option<OnboardingMode>, Option<PathBuf>), DesktopActionError> {
        let row = self
            .connection
            .query_row(
                "SELECT mode, workspace_path FROM desktop_onboarding WHERE singleton = 1",
                [],
                |row| {
                    Ok((
                        row.get::<_, Option<String>>(0)?,
                        row.get::<_, Option<String>>(1)?,
                    ))
                },
            )
            .optional()
            .map_err(|_| DesktopActionError::storage())?;
        let (mode, workspace) = row.unwrap_or((None, None));
        Ok((
            mode.as_deref().and_then(OnboardingMode::parse),
            workspace.map(PathBuf::from),
        ))
    }

    fn provider_record(&self, provider_id: &str) -> Result<ProviderRecord, DesktopActionError> {
        self.connection
            .query_row(
                "SELECT provider_id, name, base_url, key_ref, key_hint, key_configured, \
                 status, reason_code FROM local_providers WHERE provider_id = ?1",
                [provider_id],
                |row| {
                    Ok(ProviderRecord {
                        provider_id: row.get(0)?,
                        name: row.get(1)?,
                        base_url: row.get(2)?,
                        key_ref: row.get(3)?,
                        key_hint: row.get(4)?,
                        key_configured: row.get(5)?,
                        status: row.get(6)?,
                        reason_code: row.get(7)?,
                    })
                },
            )
            .optional()
            .map_err(|_| DesktopActionError::storage())?
            .ok_or_else(|| DesktopActionError::new("provider_not_found", "refresh_provider_list"))
    }

    fn provider_views(&self) -> Result<Vec<LocalProviderView>, DesktopActionError> {
        let mut statement = self
            .connection
            .prepare("SELECT provider_id FROM local_providers ORDER BY rowid")
            .map_err(|_| DesktopActionError::storage())?;
        let ids = statement
            .query_map([], |row| row.get::<_, String>(0))
            .map_err(|_| DesktopActionError::storage())?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| DesktopActionError::storage())?;
        ids.iter().map(|id| self.provider_view(id)).collect()
    }

    fn provider_view(&self, provider_id: &str) -> Result<LocalProviderView, DesktopActionError> {
        let provider = self.provider_record(provider_id)?;
        let mut statement = self
            .connection
            .prepare(
                "SELECT model_id, is_default FROM local_provider_models \
                 WHERE provider_id = ?1 ORDER BY rowid",
            )
            .map_err(|_| DesktopActionError::storage())?;
        let rows = statement
            .query_map([provider_id], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, bool>(1)?))
            })
            .map_err(|_| DesktopActionError::storage())?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| DesktopActionError::storage())?;
        let default_model = rows
            .iter()
            .find_map(|(model, is_default)| is_default.then_some(model.clone()));
        Ok(LocalProviderView {
            provider_id: provider.provider_id,
            name: provider.name,
            base_url: provider.base_url,
            key_ref: provider.key_ref,
            key_hint: provider.key_hint,
            key_configured: provider.key_configured,
            status: provider.status,
            reason_code: provider.reason_code,
            models: rows.into_iter().map(|(model, _)| model).collect(),
            default_model,
            is_active: self.is_active_provider(provider_id)?,
        })
    }

    fn default_model(&self, provider_id: &str) -> Result<Option<String>, DesktopActionError> {
        self.connection
            .query_row(
                "SELECT model_id FROM local_provider_models \
                 WHERE provider_id = ?1 AND is_default = 1",
                [provider_id],
                |row| row.get(0),
            )
            .optional()
            .map_err(|_| DesktopActionError::storage())
    }

    fn record_probe_failure(
        &self,
        provider_id: &str,
        reason_code: &'static str,
    ) -> Result<(), DesktopActionError> {
        self.connection
            .execute(
                "UPDATE local_providers SET status = 'error', reason_code = ?2 \
                 WHERE provider_id = ?1",
                params![provider_id, reason_code],
            )
            .map_err(|_| DesktopActionError::storage())?;
        Ok(())
    }
}

fn is_configuration_admission_error(error: &DesktopActionError) -> bool {
    matches!(
        error.reason_code,
        "desktop_configuration_superseded"
            | "desktop_configuration_restart_in_progress"
            | "desktop_unpublished_sidecar_cleanup_failed"
            | "desktop_state_persist_failed"
            | "desktop_stopping"
    )
}

fn migrate(connection: &Connection) -> Result<(), DesktopActionError> {
    let version: i64 = connection
        .pragma_query_value(None, "user_version", |row| row.get(0))
        .map_err(|_| DesktopActionError::storage())?;
    if version > SCHEMA_VERSION {
        return Err(DesktopActionError::new(
            "desktop_migration_incompatible",
            "upgrade_sage",
        ));
    }
    if version == 0 {
        connection.execute_batch(
            "BEGIN IMMEDIATE;
             CREATE TABLE IF NOT EXISTS desktop_onboarding (
                 singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                 mode TEXT,
                 workspace_path TEXT,
                 active_provider_id TEXT
             );
             CREATE TABLE IF NOT EXISTS local_providers (
                 provider_id TEXT PRIMARY KEY,
                 name TEXT NOT NULL,
                 base_url TEXT NOT NULL,
                 key_ref TEXT NOT NULL,
                 key_hint TEXT NOT NULL,
                 key_configured INTEGER NOT NULL,
                 status TEXT NOT NULL,
                 reason_code TEXT
             );
             CREATE TABLE IF NOT EXISTS local_provider_models (
                 provider_id TEXT NOT NULL REFERENCES local_providers(provider_id) ON DELETE CASCADE,
                 model_id TEXT NOT NULL,
                 is_default INTEGER NOT NULL DEFAULT 0,
                 PRIMARY KEY (provider_id, model_id)
             );
             CREATE TABLE IF NOT EXISTS provider_operations (
                 operation_id TEXT PRIMARY KEY,
                 kind TEXT NOT NULL,
                 provider_id TEXT NOT NULL,
                 phase TEXT NOT NULL,
                 target_key_ref TEXT NOT NULL,
                 previous_key_ref TEXT,
                 provider_name TEXT,
                 base_url TEXT,
                 default_model TEXT,
                 key_hint TEXT,
                 last_error TEXT
             );
             PRAGMA user_version = 2;
             COMMIT;",
        )
        .map_err(|_| DesktopActionError::storage())?;
    } else if version == 1 {
        connection
            .execute_batch(
                "BEGIN IMMEDIATE;
             ALTER TABLE desktop_onboarding ADD COLUMN active_provider_id TEXT;
             CREATE TABLE provider_operations (
                 operation_id TEXT PRIMARY KEY,
                 kind TEXT NOT NULL,
                 provider_id TEXT NOT NULL,
                 phase TEXT NOT NULL,
                 target_key_ref TEXT NOT NULL,
                 previous_key_ref TEXT,
                 provider_name TEXT,
                 base_url TEXT,
                 default_model TEXT,
                 key_hint TEXT,
                 last_error TEXT
             );
             PRAGMA user_version = 2;
             COMMIT;",
            )
            .map_err(|_| DesktopActionError::storage())?;
    }
    Ok(())
}

fn capability(status: &str, reason_code: Option<&str>, action: Option<&str>) -> DesktopCapability {
    DesktopCapability {
        status: status.into(),
        reason_code: reason_code.map(str::to_string),
        action: action.map(str::to_string),
    }
}

pub fn capability_matrix(
    mode: Option<OnboardingMode>,
    workspace_ready: bool,
    provider_ready: bool,
    inputs: CapabilityInputs,
) -> BTreeMap<String, DesktopCapability> {
    let mut values = BTreeMap::new();
    values.insert("data_directory".into(), capability("ready", None, None));
    values.insert("migrations".into(), capability("ready", None, None));
    values.insert(
        "workspace".into(),
        if workspace_ready {
            capability("ready", None, None)
        } else {
            capability(
                "blocked",
                Some("workspace_required"),
                Some("select_workspace"),
            )
        },
    );
    values.insert(
        "provider".into(),
        if provider_ready {
            capability("ready", None, None)
        } else if mode == Some(OnboardingMode::Cloud) {
            capability(
                "blocked",
                Some("cloud_oauth_not_available"),
                Some("choose_local_mode"),
            )
        } else {
            capability(
                "blocked",
                Some("provider_not_configured"),
                Some("configure_provider"),
            )
        },
    );
    for name in ["conversation", "rag"] {
        values.insert(
            name.into(),
            if provider_ready && workspace_ready {
                capability("ready", None, None)
            } else {
                capability(
                    "blocked",
                    Some("provider_or_workspace_required"),
                    Some("complete_onboarding"),
                )
            },
        );
    }
    values.insert(
        "side_effect_tools".into(),
        if inputs.docker_ready {
            capability("ready", None, None)
        } else {
            capability(
                "blocked",
                Some("docker_not_available"),
                Some("continue_without_side_effect_tools"),
            )
        },
    );
    values.insert(
        "postgres".into(),
        if inputs.postgres_ready {
            capability("ready", None, None)
        } else {
            capability(
                "degraded",
                Some("postgres_not_configured"),
                Some("use_sqlite_rag"),
            )
        },
    );
    values.insert(
        "web_search".into(),
        if inputs.web_search_ready {
            capability("ready", None, None)
        } else {
            capability(
                "degraded",
                Some("web_search_not_configured"),
                Some("continue_with_local_sources"),
            )
        },
    );
    values
}

fn normalized_label(value: &str, reason_code: &'static str) -> Result<String, DesktopActionError> {
    let value = value.trim();
    if value.is_empty() || value.len() > 200 {
        return Err(DesktopActionError::new(
            reason_code,
            "review_provider_settings",
        ));
    }
    Ok(value.into())
}

fn normalized_base_url(value: &str) -> Result<String, DesktopActionError> {
    let mut url = reqwest::Url::parse(value.trim()).map_err(|_| {
        DesktopActionError::new("provider_base_url_invalid", "review_provider_settings")
    })?;
    let loopback = matches!(url.host_str(), Some("127.0.0.1" | "localhost"));
    if (url.scheme() != "https" && !(loopback && url.scheme() == "http"))
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(DesktopActionError::new(
            "provider_base_url_invalid",
            "review_provider_settings",
        ));
    }
    let normalized_path = url.path().trim_end_matches('/').to_string();
    url.set_path(&normalized_path);
    Ok(url.to_string().trim_end_matches('/').to_string())
}

fn normalize_models(models: Vec<String>) -> Result<Vec<String>, DesktopActionError> {
    let mut normalized = models
        .into_iter()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty() && value.len() <= 200)
        .collect::<Vec<_>>();
    normalized.sort();
    normalized.dedup();
    if normalized.is_empty() {
        return Err(DesktopActionError::new(
            "provider_probe_invalid_response",
            "check_provider_settings",
        ));
    }
    Ok(normalized)
}

fn key_hint(secret: &str) -> String {
    let suffix = secret
        .chars()
        .rev()
        .take(4)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect::<String>();
    format!("****{suffix}")
}

#[derive(Default)]
pub struct HttpProviderProbe;

impl ProviderProbe for HttpProviderProbe {
    fn discover_models(
        &self,
        base_url: &str,
        secret: &str,
    ) -> Result<Vec<String>, ProviderProbeError> {
        let client = Client::builder()
            .timeout(Duration::from_secs(5))
            .build()
            .map_err(|_| ProviderProbeError::Unavailable)?;
        let response = client
            .get(format!("{}/models", base_url.trim_end_matches('/')))
            .bearer_auth(secret)
            .send()
            .map_err(|_| ProviderProbeError::Unavailable)?;
        if !response.status().is_success() {
            return Err(ProviderProbeError::Unavailable);
        }
        let payload: Value = response
            .json()
            .map_err(|_| ProviderProbeError::InvalidResponse)?;
        let models = payload
            .get("data")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(|item| item.get("id").and_then(Value::as_str))
                    .map(str::to_string)
                    .collect::<Vec<_>>()
            })
            .or_else(|| {
                payload
                    .get("models")
                    .and_then(Value::as_array)
                    .map(|items| {
                        items
                            .iter()
                            .filter_map(Value::as_str)
                            .map(str::to_string)
                            .collect::<Vec<_>>()
                    })
            })
            .ok_or(ProviderProbeError::InvalidResponse)?;
        if models.is_empty() {
            return Err(ProviderProbeError::InvalidResponse);
        }
        Ok(models)
    }
}

pub struct SystemHostCapabilityProbe;

impl HostCapabilityProbe for SystemHostCapabilityProbe {
    fn detect(&self) -> CapabilityInputs {
        CapabilityInputs {
            docker_ready: docker_socket_ready(),
            postgres_ready: false,
            web_search_ready: false,
        }
    }
}

#[cfg(unix)]
fn docker_socket_ready() -> bool {
    use std::io::{Read, Write};
    use std::os::unix::net::UnixStream;

    let mut candidates = vec![PathBuf::from("/var/run/docker.sock")];
    if let Some(home) = std::env::var_os("HOME") {
        candidates.push(PathBuf::from(home).join(".docker/run/docker.sock"));
    }
    candidates.into_iter().any(|path| {
        let Ok(mut stream) = UnixStream::connect(path) else {
            return false;
        };
        let _ = stream.set_read_timeout(Some(Duration::from_millis(300)));
        let _ = stream.set_write_timeout(Some(Duration::from_millis(300)));
        if stream
            .write_all(b"GET /_ping HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            .is_err()
        {
            return false;
        }
        let mut response = [0_u8; 64];
        stream
            .read(&mut response)
            .is_ok_and(|read| response[..read].starts_with(b"HTTP/1.1 200"))
    })
}

#[cfg(not(unix))]
fn docker_socket_ready() -> bool {
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lifecycle::OrphanRecord;
    use crate::state_repository::DesktopStateRepository;
    use std::collections::HashMap;
    use std::sync::{Barrier, MutexGuard};

    #[derive(Default)]
    struct BarrierSecrets {
        values: Mutex<HashMap<String, String>>,
        stored: Option<(Arc<Barrier>, Arc<Barrier>)>,
        fail_delete: Mutex<bool>,
    }

    impl SecretBroker for BarrierSecrets {
        fn key_ref(&self, account: &str) -> Result<String, SecretBrokerError> {
            Ok(format!("memory://{account}"))
        }

        fn store(&self, account: &str, secret: &str) -> Result<String, SecretBrokerError> {
            let key_ref = self.key_ref(account)?;
            self.values
                .lock()
                .unwrap()
                .insert(key_ref.clone(), secret.to_string());
            if let Some((staged, resume)) = &self.stored {
                staged.wait();
                resume.wait();
            }
            Ok(key_ref)
        }

        fn read(&self, key_ref: &str) -> Result<String, SecretBrokerError> {
            self.values
                .lock()
                .unwrap()
                .get(key_ref)
                .cloned()
                .ok_or(SecretBrokerError::Missing)
        }

        fn delete(&self, key_ref: &str) -> Result<(), SecretBrokerError> {
            if std::mem::take(&mut *self.fail_delete.lock().unwrap()) {
                return Err(SecretBrokerError::AccessDenied);
            }
            self.values
                .lock()
                .unwrap()
                .remove(key_ref)
                .map(|_| ())
                .ok_or(SecretBrokerError::Missing)
        }
    }

    struct BarrierProbe {
        completed: Arc<Barrier>,
        resume: Arc<Barrier>,
    }

    impl ProviderProbe for BarrierProbe {
        fn discover_models(
            &self,
            _base_url: &str,
            _secret: &str,
        ) -> Result<Vec<String>, ProviderProbeError> {
            self.completed.wait();
            self.resume.wait();
            Ok(vec!["model-new".into()])
        }
    }

    struct TestCapabilities;

    impl HostCapabilityProbe for TestCapabilities {
        fn detect(&self) -> CapabilityInputs {
            CapabilityInputs {
                docker_ready: false,
                postgres_ready: false,
                web_search_ready: false,
            }
        }
    }

    fn provider_action() -> DesktopOnboardingAction {
        DesktopOnboardingAction::AddProvider {
            name: "Provider".into(),
            base_url: "https://provider.example/v1".into(),
            api_key: "test-secret-value".into(),
            default_model: "model-old".into(),
        }
    }

    fn orphan() -> OrphanRecord {
        OrphanRecord {
            pid: 73,
            start_time: 173,
            executable: "/Applications/Sage.app/Contents/Resources/sidecar/sage-api".into(),
        }
    }

    fn test_host(root: &Path) -> (crate::supervisor::SharedHostState, DesktopStateRepository) {
        let repository = DesktopStateRepository::new(root.join("host-state.json"));
        let host = crate::supervisor::test_host_with_repository(repository.clone());
        (host, repository)
    }

    fn locked_runtime(state: &SharedOnboardingState) -> MutexGuard<'_, OnboardingRuntime> {
        state.0.lock().unwrap()
    }

    fn action_error(outcome: OnboardingActionOutcome) -> DesktopActionError {
        match outcome.result {
            Err(error) => error,
            Ok(_) => panic!("provider action unexpectedly succeeded"),
        }
    }

    #[test]
    fn provider_action_waiting_for_onboarding_mutex_rejects_new_write_ahead_ownership() {
        let root = tempfile::tempdir().unwrap();
        let secrets = Arc::new(BarrierSecrets::default());
        let service = OnboardingService::open_with(
            root.path().join("data"),
            secrets.clone(),
            Arc::new(BarrierProbe {
                completed: Arc::new(Barrier::new(1)),
                resume: Arc::new(Barrier::new(1)),
            }),
            Arc::new(TestCapabilities),
        )
        .unwrap();
        let state = SharedOnboardingState::from_service(service);
        let (host, repository) = test_host(root.path());
        let admitted = Arc::new(Barrier::new(2));
        let resume = Arc::new(Barrier::new(2));
        let state_lock = locked_runtime(&state);
        let worker = {
            let state = state.clone();
            let host = host.clone();
            let admitted = admitted.clone();
            let resume = resume.clone();
            std::thread::spawn(move || {
                execute_onboarding_action_with_hook(provider_action(), &state, &host, || {
                    admitted.wait();
                    resume.wait();
                })
            })
        };
        admitted.wait();
        assert!(crate::supervisor::test_reserve_unpublished(
            &host,
            &orphan()
        ));
        resume.wait();
        drop(state_lock);

        let outcome = worker.join().unwrap();
        assert_eq!(
            action_error(outcome).reason_code,
            "desktop_configuration_superseded"
        );
        assert!(secrets.values.lock().unwrap().is_empty());
        assert_eq!(repository.load_unpublished_orphans().unwrap(), [orphan()]);
    }

    #[test]
    fn probe_result_cannot_publish_metadata_after_write_ahead_ownership() {
        let root = tempfile::tempdir().unwrap();
        let completed = Arc::new(Barrier::new(2));
        let resume = Arc::new(Barrier::new(2));
        let secrets = Arc::new(BarrierSecrets::default());
        let mut service = OnboardingService::open_with(
            root.path().join("data"),
            secrets,
            Arc::new(BarrierProbe {
                completed: completed.clone(),
                resume: resume.clone(),
            }),
            Arc::new(TestCapabilities),
        )
        .unwrap();
        let provider = service
            .add_provider(match provider_action() {
                DesktopOnboardingAction::AddProvider {
                    name,
                    base_url,
                    api_key,
                    default_model,
                } => LocalProviderInput {
                    name,
                    base_url,
                    api_key,
                    default_model,
                },
                _ => unreachable!(),
            })
            .unwrap();
        let provider_id = provider.provider_id.clone();
        let state = SharedOnboardingState::from_service(service);
        let (host, _) = test_host(root.path());
        let worker = {
            let state = state.clone();
            let host = host.clone();
            let provider_id = provider_id.clone();
            std::thread::spawn(move || {
                execute_onboarding_action(
                    DesktopOnboardingAction::ProbeProvider { provider_id },
                    &state,
                    &host,
                )
            })
        };
        completed.wait();
        assert!(crate::supervisor::test_reserve_unpublished(
            &host,
            &orphan()
        ));
        resume.wait();

        let outcome = worker.join().unwrap();
        assert_eq!(
            action_error(outcome).reason_code,
            "desktop_configuration_superseded"
        );
        let runtime = locked_runtime(&state);
        let OnboardingRuntime::Ready(service) = &*runtime else {
            panic!("service not ready")
        };
        let snapshot = service.snapshot();
        assert_eq!(snapshot.providers[0].status, "untested");
        assert_eq!(snapshot.providers[0].models, ["model-old"]);
        assert!(snapshot.active_provider_id.is_none());
    }

    #[test]
    fn staged_keychain_secret_is_compensated_when_commit_lease_drifts() {
        let root = tempfile::tempdir().unwrap();
        let staged = Arc::new(Barrier::new(2));
        let resume = Arc::new(Barrier::new(2));
        let secrets = Arc::new(BarrierSecrets {
            values: Mutex::new(HashMap::new()),
            stored: Some((staged.clone(), resume.clone())),
            ..BarrierSecrets::default()
        });
        let service = OnboardingService::open_with(
            root.path().join("data"),
            secrets.clone(),
            Arc::new(BarrierProbe {
                completed: Arc::new(Barrier::new(1)),
                resume: Arc::new(Barrier::new(1)),
            }),
            Arc::new(TestCapabilities),
        )
        .unwrap();
        let state = SharedOnboardingState::from_service(service);
        let (host, repository) = test_host(root.path());
        let worker = {
            let state = state.clone();
            let host = host.clone();
            std::thread::spawn(move || execute_onboarding_action(provider_action(), &state, &host))
        };
        staged.wait();
        assert!(crate::supervisor::test_reserve_unpublished(
            &host,
            &orphan()
        ));
        resume.wait();

        let outcome = worker.join().unwrap();
        assert_eq!(
            action_error(outcome).reason_code,
            "desktop_configuration_superseded"
        );
        assert!(secrets.values.lock().unwrap().is_empty());
        assert_eq!(repository.load_unpublished_orphans().unwrap(), [orphan()]);
        let runtime = locked_runtime(&state);
        let OnboardingRuntime::Ready(service) = &*runtime else {
            panic!("service not ready")
        };
        assert!(service.snapshot().providers.is_empty());
        assert_eq!(service.pending_operation_count().unwrap(), 0);
    }

    #[test]
    fn active_cleanup_failure_returns_error_with_owned_restart_receipt() {
        let root = tempfile::tempdir().unwrap();
        let secrets = Arc::new(BarrierSecrets::default());
        let mut service = OnboardingService::open_with(
            root.path().join("data"),
            secrets.clone(),
            Arc::new(BarrierProbe {
                completed: Arc::new(Barrier::new(1)),
                resume: Arc::new(Barrier::new(1)),
            }),
            Arc::new(TestCapabilities),
        )
        .unwrap();
        let provider = service
            .add_provider(match provider_action() {
                DesktopOnboardingAction::AddProvider {
                    name,
                    base_url,
                    api_key,
                    default_model,
                } => LocalProviderInput {
                    name,
                    base_url,
                    api_key,
                    default_model,
                },
                _ => unreachable!(),
            })
            .unwrap();
        service.probe_provider(&provider.provider_id).unwrap();
        service.set_active_provider(&provider.provider_id).unwrap();
        *secrets.fail_delete.lock().unwrap() = true;
        let state = SharedOnboardingState::from_service(service);
        let (host, _) = test_host(root.path());

        let outcome = execute_onboarding_action(
            DesktopOnboardingAction::RotateProviderKey {
                provider_id: provider.provider_id,
                api_key: "test-secret-rotated".into(),
            },
            &state,
            &host,
        );

        assert!(outcome.restart.is_some());
        assert_eq!(
            action_error(outcome).reason_code,
            "provider_reconciliation_required"
        );
    }
}
