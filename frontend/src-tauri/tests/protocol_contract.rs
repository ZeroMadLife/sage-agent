use sage_desktop_lib::protocol::{
    validate_handshake, DesktopSession, ExpectedHandshake, Handshake,
};

fn expected() -> ExpectedHandshake {
    ExpectedHandshake {
        child_pid: 42,
        instance_id: "instance".into(),
        nonce: "nonce".into(),
        api_version: "1".into(),
        build_sha: "build".into(),
    }
}

#[test]
fn desktop_session_debug_output_redacts_the_bearer() {
    let session = DesktopSession {
        endpoint: "http://127.0.0.1:49152".into(),
        bearer: "must-not-be-logged".into(),
        instance_id: "instance".into(),
    };
    let diagnostic = format!("{session:?}");
    assert!(diagnostic.contains("[REDACTED]"));
    assert!(!diagnostic.contains("must-not-be-logged"));
}

fn valid() -> Handshake {
    Handshake {
        pid: 42,
        port: 49152,
        instance_id: "instance".into(),
        api_version: "1".into(),
        build_sha: "build".into(),
        nonce: "nonce".into(),
    }
}

#[test]
fn valid_handshake_exposes_loopback_only_after_all_fields_match() {
    let endpoint = validate_handshake(&valid(), &expected()).unwrap();
    assert_eq!(endpoint, "http://127.0.0.1:49152");
}

#[test]
fn rejects_pid_nonce_build_api_and_forged_port() {
    type HandshakeMutation = (&'static str, Box<dyn Fn(&mut Handshake)>);
    let mutations: Vec<HandshakeMutation> = vec![
        ("desktop_pid_mismatch", Box::new(|value| value.pid = 41)),
        (
            "desktop_nonce_mismatch",
            Box::new(|value| value.nonce = "wrong".into()),
        ),
        (
            "desktop_build_mismatch",
            Box::new(|value| value.build_sha = "old".into()),
        ),
        (
            "desktop_api_incompatible",
            Box::new(|value| value.api_version = "0".into()),
        ),
        ("desktop_port_rejected", Box::new(|value| value.port = 0)),
    ];
    for (reason, mutate) in mutations {
        let mut handshake = valid();
        mutate(&mut handshake);
        assert_eq!(
            validate_handshake(&handshake, &expected())
                .unwrap_err()
                .reason_code(),
            reason
        );
    }
}

#[test]
fn handshake_schema_rejects_unknown_or_missing_fields() {
    let unknown = r#"{"pid":42,"port":1,"instance_id":"i","api_version":"1","build_sha":"b","nonce":"n","bearer":"leak"}"#;
    let missing = r#"{"pid":42,"port":1,"instance_id":"i","api_version":"1","build_sha":"b"}"#;
    assert!(serde_json::from_str::<Handshake>(unknown).is_err());
    assert!(serde_json::from_str::<Handshake>(missing).is_err());
}
