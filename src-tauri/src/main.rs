#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{net::{SocketAddr, TcpStream}, path::PathBuf, process::{Child, Command}, sync::Mutex, time::Duration};

struct BackendState(Mutex<Option<Child>>);

fn backend_dir() -> PathBuf {
    // CARGO_MANIFEST_DIR는 `<project>/src-tauri`이므로, 한 단계 위가 프로젝트 루트다.
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("src-tauri parent")
        .join("backend")
}

fn backend_is_listening() -> bool {
    let address: SocketAddr = "127.0.0.1:8788".parse().expect("valid sidecar address");
    TcpStream::connect_timeout(&address, Duration::from_millis(150)).is_ok()
}

#[tauri::command]
fn start_backend(state: tauri::State<'_, BackendState>) -> Result<String, String> {
    let mut child = state.0.lock().map_err(|_| "backend state lock failed")?;
    if child.as_mut().is_some_and(|process| process.try_wait().ok().flatten().is_none()) { return Ok("http://127.0.0.1:8788".into()); }
    // 이전 개발 실행에서 남은 정상 sidecar가 포트를 점유할 수 있다.
    // 포트만 재사용하고 같은 서버를 하나 더 띄우지 않는다.
    if backend_is_listening() { return Ok("http://127.0.0.1:8788".into()); }
    let directory = backend_dir();
    let python = std::env::var("AVIS_BACKEND_PYTHON").map(PathBuf::from).unwrap_or_else(|_| directory.join(".venv/bin/python"));
    // 개발 환경의 가상환경 인터프리터가 사라진 경우 uv의 프로젝트 환경으로 복구한다.
    let mut command = if python.is_file() {
        let mut value = Command::new(&python);
        value.args(["-m", "api.server", "--port", "8788"]);
        value
    } else if std::env::var("AVIS_BACKEND_PYTHON").is_ok() {
        return Err(format!("Python sidecar를 찾을 수 없습니다: {}", python.display()));
    } else {
        let mut value = Command::new("uv");
        value.args(["run", "python", "-m", "api.server", "--port", "8788"]);
        value
    };
    let process = command.current_dir(&directory).spawn().map_err(|error| format!("Python sidecar 실행 실패: {error}"))?;
    *child = Some(process);
    Ok("http://127.0.0.1:8788".into())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(BackendState(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![start_backend])
        .run(tauri::generate_context!())
        .expect("error while running Arvis Check");
}
