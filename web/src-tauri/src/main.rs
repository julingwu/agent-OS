#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// The shell is a thin WebView: everything (auth, chat, org tree, task cards)
// lives in the bundled React bundle and talks to the local FastAPI backend.
//
// v1.1: the exe also *starts* that backend. Double-clicking used to give a white
// screen whenever 8077 was down; now we spawn uvicorn (no console window), the
// UI waits for it, and we kill our child when the window closes so nothing is
// left orphaned.
use std::net::TcpStream;
use std::os::windows::process::CommandExt;
use std::path::Path;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const PORT: u16 = 8077;

static CHILD: Mutex<Option<Child>> = Mutex::new(None);

fn backend_up() -> bool {
    let addr = format!("127.0.0.1:{PORT}");
    match addr.parse() {
        Ok(sock) => TcpStream::connect_timeout(&sock, Duration::from_millis(300)).is_ok(),
        Err(_) => false,
    }
}

fn start_backend() {
    if backend_up() {
        return; // someone already runs it (dev machine, start_all.ps1)
    }
    let root = std::env::var("OA_ROOT").unwrap_or_else(|_| "C:\\oaagent-m0".to_string());
    let candidates = [
        format!("{root}\\.venv\\Scripts\\python.exe"),
        format!("{root}\\venv\\Scripts\\python.exe"),
    ];
    let Some(python) = candidates.iter().find(|p| Path::new(p).exists()) else {
        return;
    };
    let spawned = Command::new(python)
        .args([
            "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8077",
            "--log-level", "warning",
        ])
        .current_dir(&root)
        .creation_flags(CREATE_NO_WINDOW)
        .spawn();
    if let Ok(child) = spawned {
        if let Ok(mut slot) = CHILD.lock() {
            *slot = Some(child);
        }
    }
}

fn stop_backend() {
    if let Ok(mut slot) = CHILD.lock() {
        if let Some(child) = slot.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
        *slot = None;
    }
}

fn main() {
    tauri::Builder::default()
        .setup(|_app| {
            start_backend();
            Ok(())
        })
        .on_window_event(|_window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                stop_backend();
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to start Agent OS console");
}
