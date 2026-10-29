// Desktop shell of Dubbing Studio.
//
// The window first shows a small bundled page (desktop/setup). On the first
// launch the bundled uv creates the app's environment from the bundled Python
// (uv never installs Python itself) and ffmpeg is downloaded; then the shell
// starts the Python app (pipeline_ui.py on a free port) and opens the page
// that app serves. Closing the window stops the server, its
// running job and the warm voice model.
//
// Folders:
//   resources/app      the Python app (read-only, replaced on update)
//   resources/python   Python 3.12 (python-build-standalone) for every environment
//   %LOCALAPPDATA%\com.dubbingstudio.app
//                      venv, Python, uv cache, ffmpeg, engine environments, settings, logs
//   Documents\Dubbing Studio
//                      projects and the results of the tools

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::{self, File};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, RunEvent};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

struct AppPaths {
    app: PathBuf,
    python: PathBuf,
    data: PathBuf,
    library: PathBuf,
    uv: PathBuf,
    logs: PathBuf,
}

#[derive(Default)]
struct Server {
    child: Mutex<Option<Child>>,
    url: Mutex<Option<String>>,
}

#[derive(Serialize)]
struct Status {
    ready: bool,
    gpu: bool,
    ffmpeg: bool,
    data_dir: String,
    library_dir: String,
}

#[derive(Clone, Serialize)]
struct LogLine {
    step: String,
    line: String,
}

fn text<E: std::fmt::Display>(error: E) -> String {
    error.to_string()
}

fn app_paths(app: &AppHandle) -> Result<AppPaths, String> {
    let resources = app.path().resource_dir().map_err(text)?;
    let data = app.path().app_local_data_dir().map_err(text)?;
    let library = app.path().document_dir().map_err(text)?.join("Dubbing Studio");
    let exe = std::env::current_exe().map_err(text)?;
    let exe_dir = exe.parent().ok_or("the program folder is unknown")?;
    let uv = exe_dir.join(if cfg!(windows) { "uv.exe" } else { "uv" });
    let logs = data.join("logs");
    Ok(AppPaths { app: resources.join("app"), python: resources.join("python"), data, library, uv, logs })
}

fn bundled_python(paths: &AppPaths) -> PathBuf {
    if cfg!(windows) {
        paths.python.join("python.exe")
    } else {
        paths.python.join("bin").join("python3")
    }
}

fn venv_python(paths: &AppPaths) -> PathBuf {
    let venv = paths.data.join("venv");
    if cfg!(windows) {
        venv.join("Scripts").join("python.exe")
    } else {
        venv.join("bin").join("python")
    }
}

fn ffmpeg_dir(paths: &AppPaths) -> PathBuf {
    paths.data.join("tools").join("ffmpeg").join("bin")
}

fn in_path(name: &str) -> bool {
    let file = if cfg!(windows) { format!("{name}.exe") } else { name.to_string() };
    std::env::var_os("PATH")
        .map(|path| std::env::split_paths(&path).any(|dir| dir.join(&file).is_file()))
        .unwrap_or(false)
}

fn ffmpeg_ready(paths: &AppPaths) -> bool {
    let local = ffmpeg_dir(paths);
    let exe = if cfg!(windows) { "ffmpeg.exe" } else { "ffmpeg" };
    local.join(exe).is_file() || (in_path("ffmpeg") && in_path("ffprobe"))
}

fn quiet(command: &mut Command) {
    // No console windows: the server and everything it starts stay invisible.
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    #[cfg(not(windows))]
    let _ = command;
}

fn has_nvidia_gpu() -> bool {
    let mut command = Command::new("nvidia-smi");
    command.arg("-L").stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    quiet(&mut command);
    command.status().map(|status| status.success()).unwrap_or(false)
}

/// What the environment was built from; a new lock file or Python in an update rebuilds it.
fn environment_stamp(paths: &AppPaths, gpu: bool) -> String {
    let python = fs::read_to_string(paths.python.join(".version")).unwrap_or_default();
    let lock = fs::read_to_string(paths.app.join("uv.lock")).unwrap_or_default();
    format!("gpu={gpu}\npython={}\n{lock}", python.trim())
}

fn stamp_file(paths: &AppPaths) -> PathBuf {
    paths.data.join("venv").join(".dubbing-studio-lock")
}

/// The bundled uv: cache in the data folder, the bundled Python as the default
/// interpreter (engine installs use it too), nothing in the registry or in ~/.local/bin.
fn uv_environment(command: &mut Command, paths: &AppPaths) {
    command
        .env("UV_CACHE_DIR", paths.data.join("uv-cache"))
        .env("UV_PYTHON", bundled_python(paths))
        .env("UV_PYTHON_INSTALL_DIR", paths.data.join("python"))
        .env("UV_PYTHON_INSTALL_REGISTRY", "0")
        .env("UV_PYTHON_INSTALL_BIN", "0");
}

fn server_environment(command: &mut Command, paths: &AppPaths) {
    let mut folders = vec![ffmpeg_dir(paths)];
    if let Some(uv_dir) = paths.uv.parent() {
        folders.push(uv_dir.to_path_buf());
    }
    if let Some(path) = std::env::var_os("PATH") {
        folders.extend(std::env::split_paths(&path));
    }
    if let Ok(path) = std::env::join_paths(folders) {
        command.env("PATH", path);
    }
    command
        .env("DUB_DATA_DIR", &paths.data)
        .env("DUB_LIBRARY_DIR", &paths.library)
        .env("DUB_DESKTOP", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .env("PYTHONUNBUFFERED", "1");
    uv_environment(command, paths);
}

/// Read a child's output line by line (lossy UTF-8, so odd bytes never stop the reader).
fn forward_lines<R: Read + Send + 'static>(stream: R, sender: mpsc::Sender<String>) {
    thread::spawn(move || {
        let mut reader = BufReader::new(stream);
        let mut buffer = Vec::new();
        loop {
            buffer.clear();
            match reader.read_until(b'\n', &mut buffer) {
                Ok(0) | Err(_) => break,
                Ok(_) => {
                    let line = String::from_utf8_lossy(&buffer).trim_end().to_string();
                    if sender.send(line).is_err() {
                        break;
                    }
                }
            }
        }
    });
}

fn run_step(app: &AppHandle, mut command: Command, step: &str, log: &mut File) -> Result<(), String> {
    command.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
    quiet(&mut command);
    let _ = writeln!(log, "> {command:?}");
    let mut child = command.spawn().map_err(|error| format!("{step}: {error}"))?;
    let (sender, receiver) = mpsc::channel();
    forward_lines(child.stdout.take().expect("piped stdout"), sender.clone());
    forward_lines(child.stderr.take().expect("piped stderr"), sender);
    for line in receiver {
        let _ = writeln!(log, "{line}");
        let _ = app.emit("setup-log", LogLine { step: step.to_string(), line });
    }
    let status = child.wait().map_err(text)?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("{step}: {status}"))
    }
}

#[tauri::command]
fn setup_status(app: AppHandle) -> Result<Status, String> {
    let paths = app_paths(&app)?;
    let gpu = has_nvidia_gpu();
    let built = fs::read_to_string(stamp_file(&paths)).map(|stamp| stamp == environment_stamp(&paths, gpu));
    let ffmpeg = ffmpeg_ready(&paths);
    Ok(Status {
        ready: venv_python(&paths).is_file() && built.unwrap_or(false) && ffmpeg,
        gpu,
        ffmpeg,
        data_dir: paths.data.display().to_string(),
        library_dir: paths.library.display().to_string(),
    })
}

fn setup_blocking(app: &AppHandle) -> Result<(), String> {
    let paths = app_paths(app)?;
    fs::create_dir_all(&paths.logs).map_err(text)?;
    fs::create_dir_all(&paths.library).map_err(text)?;
    let mut log = File::create(paths.logs.join("setup.log")).map_err(text)?;
    let gpu = has_nvidia_gpu();

    // 1. The app's environment (Whisper, numpy, ...) from the bundled Python and the shipped lock file.
    let mut sync = Command::new(&paths.uv);
    sync.arg("sync")
        .arg("--project")
        .arg(&paths.app)
        .args(["--frozen", "--no-install-project", "--python"])
        .arg(bundled_python(&paths));
    if gpu {
        sync.args(["--extra", "cuda"]); // cuBLAS for Whisper on the GPU
    }
    uv_environment(&mut sync, &paths);
    sync.env("UV_PROJECT_ENVIRONMENT", paths.data.join("venv"))
        .env("UV_PYTHON_DOWNLOADS", "never")
        .current_dir(&paths.app);
    run_step(app, sync, "python", &mut log)?;

    // 2. ffmpeg, unless the computer already has it.
    if !ffmpeg_ready(&paths) {
        let mut ffmpeg = Command::new(venv_python(&paths));
        ffmpeg.arg(paths.app.join("tools_setup.py")).arg("ffmpeg").current_dir(&paths.app);
        server_environment(&mut ffmpeg, &paths);
        run_step(app, ffmpeg, "ffmpeg", &mut log)?;
    }

    fs::write(stamp_file(&paths), environment_stamp(&paths, gpu)).map_err(text)?;
    Ok(())
}

#[tauri::command]
async fn run_setup(app: AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || setup_blocking(&app)).await.map_err(text)?
}

fn start_server_blocking(app: &AppHandle) -> Result<String, String> {
    let state = app.state::<Server>();
    {
        let mut child = state.child.lock().unwrap();
        let alive = child.as_mut().map(|process| matches!(process.try_wait(), Ok(None))).unwrap_or(false);
        if alive {
            if let Some(url) = state.url.lock().unwrap().clone() {
                return Ok(url);
            }
        }
    }
    let paths = app_paths(app)?;
    fs::create_dir_all(&paths.logs).map_err(text)?;
    let log = Arc::new(Mutex::new(File::create(paths.logs.join("server.log")).map_err(text)?));

    let mut command = Command::new(venv_python(&paths));
    command
        .arg(paths.app.join("pipeline_ui.py"))
        .args(["--port", "0", "--exit-with-stdin"])
        .current_dir(&paths.app)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    server_environment(&mut command, &paths);
    quiet(&mut command);
    let mut child = command.spawn().map_err(|error| format!("Python: {error}"))?;

    let (lines, receiver) = mpsc::channel();
    forward_lines(child.stdout.take().expect("piped stdout"), lines.clone());
    forward_lines(child.stderr.take().expect("piped stderr"), lines);
    let (found, url_receiver) = mpsc::channel();
    let writer = Arc::clone(&log);
    thread::spawn(move || {
        for line in receiver {
            if let Some(url) = line.strip_prefix("Dubbing Studio: ") {
                let _ = found.send(url.trim().to_string());
            }
            let _ = writeln!(writer.lock().unwrap(), "{line}");
        }
    });

    match url_receiver.recv_timeout(Duration::from_secs(90)) {
        Ok(url) => {
            *state.child.lock().unwrap() = Some(child);
            *state.url.lock().unwrap() = Some(url.clone());
            Ok(url)
        }
        Err(_) => {
            let _ = child.kill();
            Err(format!("The app did not start. Details: {}", paths.logs.join("server.log").display()))
        }
    }
}

#[tauri::command]
async fn start_server(app: AppHandle) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || start_server_blocking(&app)).await.map_err(text)?
}

#[tauri::command]
fn open_logs(app: AppHandle) -> Result<(), String> {
    let paths = app_paths(&app)?;
    fs::create_dir_all(&paths.logs).map_err(text)?;
    let opener = if cfg!(windows) { "explorer" } else if cfg!(target_os = "macos") { "open" } else { "xdg-open" };
    Command::new(opener).arg(&paths.logs).spawn().map_err(text)?;
    Ok(())
}

fn post_shutdown(url: &str) -> std::io::Result<()> {
    let address = url.trim_start_matches("http://").trim_end_matches('/');
    let mut stream = TcpStream::connect(address)?;
    stream.set_read_timeout(Some(Duration::from_secs(3)))?;
    write!(
        stream,
        "POST /api/shutdown HTTP/1.1\r\nHost: {address}\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{{}}"
    )?;
    let mut answer = [0u8; 256];
    let _ = stream.read(&mut answer);
    Ok(())
}

/// Ask the server to stop its job and the warm voice model, then make sure it is gone.
fn stop_server(app: &AppHandle) {
    let state = app.state::<Server>();
    let url = state.url.lock().unwrap().take();
    let child = state.child.lock().unwrap().take();
    if let Some(mut child) = child {
        if let Some(url) = url {
            let _ = post_shutdown(&url);
        }
        drop(child.stdin.take()); // stdin EOF also makes the server quit
        let deadline = Instant::now() + Duration::from_secs(15);
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                return;
            }
            thread::sleep(Duration::from_millis(100));
        }
        let _ = child.kill();
    }
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // A second launch just brings the open window forward.
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .manage(Server::default())
        .invoke_handler(tauri::generate_handler![setup_status, run_setup, start_server, open_logs])
        .build(tauri::generate_context!())
        .expect("error while building Dubbing Studio");
    app.run(|app, event| {
        if let RunEvent::Exit = event {
            stop_server(app);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn stamp_changes_with_gpu_and_lock() {
        let dir = std::env::temp_dir().join("dubbing-studio-stamp-test");
        let _ = fs::create_dir_all(dir.join("app"));
        fs::write(dir.join("app").join("uv.lock"), "lock v1").unwrap();
        let paths = AppPaths {
            app: dir.join("app"),
            python: dir.join("python"),
            data: dir.join("data"),
            library: dir.join("library"),
            uv: dir.join("uv"),
            logs: dir.join("logs"),
        };
        let cpu = environment_stamp(&paths, false);
        assert_ne!(cpu, environment_stamp(&paths, true));
        fs::write(dir.join("app").join("uv.lock"), "lock v2").unwrap();
        assert_ne!(cpu, environment_stamp(&paths, false));
        assert!(Path::new(&dir).exists());
    }
}
