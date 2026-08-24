pub mod lifecycle;
pub mod protocol;
mod supervisor;

use lifecycle::{lifecycle_action, LifecycleAction, LifecycleEvent};
use supervisor::{desktop_exit, desktop_host_status, desktop_open_diagnostics, SharedHostState};
use tauri::{Manager, RunEvent, WindowEvent};

fn allow_navigation(url: &tauri::Url) -> bool {
    (url.scheme() == "tauri" && url.host_str() == Some("localhost"))
        || (cfg!(debug_assertions)
            && matches!(url.scheme(), "http" | "https")
            && matches!(url.host_str(), Some("127.0.0.1" | "localhost")))
}

pub fn run() {
    let single_instance = tauri_plugin_single_instance::init(|app, _, _| {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.show();
            let _ = window.set_focus();
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
        }
    });
}

#[cfg(test)]
mod tests {
    use super::allow_navigation;

    #[test]
    fn navigation_guard_rejects_remote_hosts() {
        assert!(!allow_navigation(&"https://example.com".parse().unwrap()));
        assert!(allow_navigation(&"tauri://localhost".parse().unwrap()));
    }
}
