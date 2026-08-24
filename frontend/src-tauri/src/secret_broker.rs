use serde::Serialize;

pub trait SecretBroker: Send + Sync {
    fn store(&self, account: &str, secret: &str) -> Result<String, SecretBrokerError>;
    fn read(&self, key_ref: &str) -> Result<String, SecretBrokerError>;
    fn delete(&self, key_ref: &str) -> Result<(), SecretBrokerError>;
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SecretBrokerError {
    Locked,
    AccessDenied,
    Missing,
    Invalid,
    Unavailable,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct BrokerErrorResponse {
    pub reason_code: &'static str,
    pub action: &'static str,
}

pub fn broker_error(error: SecretBrokerError) -> BrokerErrorResponse {
    match error {
        SecretBrokerError::Locked => BrokerErrorResponse {
            reason_code: "keychain_locked",
            action: "unlock_keychain_and_retry",
        },
        SecretBrokerError::AccessDenied => BrokerErrorResponse {
            reason_code: "keychain_access_denied",
            action: "allow_keychain_access_and_retry",
        },
        SecretBrokerError::Missing => BrokerErrorResponse {
            reason_code: "keychain_entry_missing",
            action: "reenter_provider_key",
        },
        SecretBrokerError::Invalid => BrokerErrorResponse {
            reason_code: "keychain_request_invalid",
            action: "review_provider_settings",
        },
        SecretBrokerError::Unavailable => BrokerErrorResponse {
            reason_code: "keychain_unavailable",
            action: "retry_keychain_operation",
        },
    }
}

#[cfg(target_os = "macos")]
pub struct MacOsKeychain {
    service: String,
}

#[cfg(target_os = "macos")]
impl MacOsKeychain {
    pub fn sage_local_provider() -> Self {
        Self::with_service("com.sage.learning.local-provider")
    }

    pub fn with_service(service: impl Into<String>) -> Self {
        Self {
            service: service.into(),
        }
    }

    fn account<'a>(&self, key_ref: &'a str) -> Result<&'a str, SecretBrokerError> {
        key_ref
            .strip_prefix(&format!("keychain://{}/", self.service))
            .filter(|value| !value.is_empty() && !value.contains('/'))
            .ok_or(SecretBrokerError::Invalid)
    }

    fn entry(&self, account: &str) -> Result<keyring::Entry, SecretBrokerError> {
        keyring::Entry::new(&self.service, account).map_err(classify_keyring_error)
    }
}

#[cfg(target_os = "macos")]
impl SecretBroker for MacOsKeychain {
    fn store(&self, account: &str, secret: &str) -> Result<String, SecretBrokerError> {
        if account.is_empty() || account.contains('/') || secret.is_empty() {
            return Err(SecretBrokerError::Invalid);
        }
        self.entry(account)?
            .set_password(secret)
            .map_err(classify_keyring_error)?;
        Ok(format!("keychain://{}/{account}", self.service))
    }

    fn read(&self, key_ref: &str) -> Result<String, SecretBrokerError> {
        let account = self.account(key_ref)?;
        self.entry(account)?
            .get_password()
            .map_err(classify_keyring_error)
    }

    fn delete(&self, key_ref: &str) -> Result<(), SecretBrokerError> {
        let account = self.account(key_ref)?;
        self.entry(account)?
            .delete_credential()
            .map_err(classify_keyring_error)
    }
}

#[cfg(target_os = "macos")]
fn classify_keyring_error(error: keyring::Error) -> SecretBrokerError {
    use keyring::Error;

    match error {
        Error::NoEntry => SecretBrokerError::Missing,
        Error::NoStorageAccess(source) => classify_security_error(source.as_ref(), true),
        Error::PlatformFailure(source) => classify_security_error(source.as_ref(), false),
        Error::Invalid(_, _) | Error::TooLong(_, _) | Error::BadEncoding(_) => {
            SecretBrokerError::Invalid
        }
        Error::Ambiguous(_) => SecretBrokerError::Unavailable,
        _ => SecretBrokerError::Unavailable,
    }
}

#[cfg(target_os = "macos")]
fn classify_security_error(
    source: &(dyn std::error::Error + 'static),
    storage_access: bool,
) -> SecretBrokerError {
    let code = source
        .downcast_ref::<security_framework::base::Error>()
        .map(|error| error.code());
    match code {
        Some(-25308) => SecretBrokerError::Locked,
        Some(-25293 | -128) => SecretBrokerError::AccessDenied,
        _ if storage_access => SecretBrokerError::Locked,
        _ => SecretBrokerError::Unavailable,
    }
}
