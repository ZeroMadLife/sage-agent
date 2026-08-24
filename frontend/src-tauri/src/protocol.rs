use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Clone)]
pub struct ExpectedHandshake {
    pub child_pid: u32,
    pub instance_id: String,
    pub nonce: String,
    pub api_version: String,
    pub build_sha: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Handshake {
    pub pid: u32,
    pub port: u16,
    pub instance_id: String,
    pub api_version: String,
    pub build_sha: String,
    pub nonce: String,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum HandshakeError {
    #[error("sidecar process identity did not match")]
    Pid,
    #[error("sidecar nonce did not match")]
    Nonce,
    #[error("sidecar build identity did not match")]
    Build,
    #[error("sidecar API version is incompatible")]
    ApiVersion,
    #[error("sidecar port was rejected")]
    Port,
    #[error("sidecar instance did not match")]
    Instance,
}

impl HandshakeError {
    pub fn reason_code(&self) -> &'static str {
        match self {
            Self::Pid => "desktop_pid_mismatch",
            Self::Nonce => "desktop_nonce_mismatch",
            Self::Build => "desktop_build_mismatch",
            Self::ApiVersion => "desktop_api_incompatible",
            Self::Port => "desktop_port_rejected",
            Self::Instance => "desktop_instance_mismatch",
        }
    }
}

pub fn validate_handshake(
    handshake: &Handshake,
    expected: &ExpectedHandshake,
) -> Result<String, HandshakeError> {
    if handshake.pid != expected.child_pid {
        return Err(HandshakeError::Pid);
    }
    if handshake.instance_id != expected.instance_id {
        return Err(HandshakeError::Instance);
    }
    if handshake.nonce != expected.nonce {
        return Err(HandshakeError::Nonce);
    }
    if handshake.api_version != expected.api_version {
        return Err(HandshakeError::ApiVersion);
    }
    if handshake.build_sha != expected.build_sha {
        return Err(HandshakeError::Build);
    }
    if handshake.port < 1024 {
        return Err(HandshakeError::Port);
    }
    Ok(format!("http://127.0.0.1:{}", handshake.port))
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopSession {
    pub endpoint: String,
    pub bearer: String,
    pub instance_id: String,
}

impl std::fmt::Debug for DesktopSession {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("DesktopSession")
            .field("endpoint", &self.endpoint)
            .field("bearer", &"[REDACTED]")
            .field("instance_id", &self.instance_id)
            .finish()
    }
}
