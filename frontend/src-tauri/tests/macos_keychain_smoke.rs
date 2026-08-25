#![cfg(target_os = "macos")]

use sage_desktop_lib::secret_broker::{MacOsKeychain, SecretBroker, SecretBrokerError};
use uuid::Uuid;

struct Cleanup<'a> {
    broker: &'a MacOsKeychain,
    key_ref: String,
}

impl Drop for Cleanup<'_> {
    fn drop(&mut self) {
        let _ = self.broker.delete(&self.key_ref);
    }
}

#[test]
fn macos_keychain_uses_a_unique_temporary_service_and_cleans_it() {
    let suffix = Uuid::new_v4().to_string();
    let broker = MacOsKeychain::with_service(format!("com.sage.learning.test.{suffix}"));
    let account = format!("provider-{suffix}");
    let secret = format!("temporary-secret-{suffix}");
    let key_ref = broker.store(&account, &secret).unwrap();
    let cleanup = Cleanup {
        broker: &broker,
        key_ref: key_ref.clone(),
    };

    assert_eq!(broker.read(&key_ref).unwrap(), secret);
    broker.store(&account, "temporary-rotated-secret").unwrap();
    assert_eq!(broker.read(&key_ref).unwrap(), "temporary-rotated-secret");
    broker.delete(&key_ref).unwrap();
    assert!(matches!(
        broker.read(&key_ref),
        Err(SecretBrokerError::Missing)
    ));

    drop(cleanup);
}
