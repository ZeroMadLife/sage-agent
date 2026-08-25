use sage_desktop_lib::secret_broker::{broker_error, SecretBrokerError};

#[test]
fn keychain_lock_and_access_denial_have_distinct_recovery_actions() {
    let locked = broker_error(SecretBrokerError::Locked);
    assert_eq!(locked.reason_code, "keychain_locked");
    assert_eq!(locked.action, "unlock_keychain_and_retry");

    let denied = broker_error(SecretBrokerError::AccessDenied);
    assert_eq!(denied.reason_code, "keychain_access_denied");
    assert_eq!(denied.action, "allow_keychain_access_and_retry");

    let missing = broker_error(SecretBrokerError::Missing);
    assert_eq!(missing.reason_code, "keychain_entry_missing");
    assert_eq!(missing.action, "reenter_provider_key");
}
