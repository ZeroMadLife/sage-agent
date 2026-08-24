pub mod diagnostics;
pub mod lifecycle;
pub mod onboarding;
pub mod protocol;
pub mod secret_broker;
pub mod state_repository;
mod supervisor;

use lifecycle::{
    apply_single_instance_action, lifecycle_action, single_instance_action, LifecycleAction,
    LifecycleEvent,
};
use supervisor::{desktop_exit, desktop_host_status, desktop_open_diagnostics, SharedHostState};
use tauri::{Manager, RunEvent, WindowEvent};

fn allow_navigation_for_profile(url: &tauri::Url, debug: bool) -> bool {
    let no_credentials = url.username().is_empty() && url.password().is_none();
    if debug {
        url.scheme() == "http"
            && url.host_str() == Some("127.0.0.1")
            && url.port() == Some(5173)
            && no_credentials
    } else {
        url.scheme() == "tauri"
            && url.host_str() == Some("localhost")
            && url.port().is_none()
            && no_credentials
    }
}

fn allow_navigation(url: &tauri::Url) -> bool {
    allow_navigation_for_profile(url, cfg!(debug_assertions))
}

pub fn run() {
    let single_instance = tauri_plugin_single_instance::init(|app, _, _| {
        if let Some(window) = app.get_webview_window("main") {
            apply_single_instance_action(
                single_instance_action(),
                || {
                    let _ = window.show();
                },
                || {
                    let _ = window.set_focus();
                },
            );
        }
    });
    let navigation_guard = tauri::plugin::Builder::<tauri::Wry, ()>::new("navigation-guard")
        .on_navigation(|_, url| allow_navigation(url))
        .build();

    let app = tauri::Builder::default()
        .plugin(single_instance)
        .plugin(navigation_guard)
        .plugin(tauri_plugin_shell::init())
        .manage(SharedHostState::default())
        .invoke_handler(tauri::generate_handler![
            desktop_host_status,
            desktop_exit,
            desktop_open_diagnostics
        ])
        .setup(|app| {
            supervisor::start(app.handle().clone());
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                let state = window.state::<SharedHostState>();
                if lifecycle_action(LifecycleEvent::WindowHidden) == LifecycleAction::KeepRunning
                    && !state.is_stopping()
                {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("Sage desktop host failed");
    app.run(|app, event| {
        if let RunEvent::ExitRequested { api, .. } = event {
            let state = app.state::<SharedHostState>().inner().clone();
            if !state.is_stopping() {
                api.prevent_exit();
                supervisor::request_exit(app.clone(), state);
            }
        } else if let RunEvent::Exit = event {
            let state = app.state::<SharedHostState>().inner().clone();
            supervisor::finalize_exit(state);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::allow_navigation_for_profile;

    #[test]
    fn navigation_guard_accepts_only_the_profile_origin() {
        assert!(allow_navigation_for_profile(
            &"tauri://localhost/status".parse().unwrap(),
            false
        ));
        assert!(!allow_navigation_for_profile(
            &"http://127.0.0.1:5173".parse().unwrap(),
            false
        ));
        assert!(allow_navigation_for_profile(
            &"http://127.0.0.1:5173/status".parse().unwrap(),
            true
        ));
        for rejected in [
            "http://127.0.0.1:5174",
            "http://localhost:5173",
            "https://127.0.0.1:5173",
            "https://example.com",
        ] {
            assert!(!allow_navigation_for_profile(
                &rejected.parse().unwrap(),
                true
            ));
        }
    }
}
