#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            open_sample_image,
            load_template,
            save_template
        ])
        .run(tauri::generate_context!())
        .expect("error while running Image OCR to Excel");
}

use serde::Deserialize;
use serde_json::{json, Value};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;

#[tauri::command]
fn open_sample_image() -> Result<String, String> {
    Err("File selection bridge is not connected yet.".into())
}

#[tauri::command]
fn load_template(app: AppHandle, path: Option<String>) -> Result<Option<String>, String> {
    let path = match path {
        Some(path) => PathBuf::from(path),
        None => match app
            .dialog()
            .file()
            .add_filter("Template JSON", &["json"])
            .blocking_pick_file()
        {
            Some(path) => path.into_path().map_err(|error| error.to_string())?,
            None => return Ok(None),
        },
    };

    let payload = json!({ "path": path.display().to_string() });
    let data = run_python_bridge("template_load", payload)?;
    serde_json::to_string(&data)
        .map(Some)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn save_template(app: AppHandle, draft: String) -> Result<Option<String>, String> {
    let draft: Value = serde_json::from_str(&draft).map_err(|error| error.to_string())?;
    let template_name = draft
        .get("template_name")
        .and_then(|value| value.as_str())
        .unwrap_or("ocr-template");
    let default_file_name = format!("{}.json", safe_file_stem(template_name));
    let save_path = match app
        .dialog()
        .file()
        .add_filter("Template JSON", &["json"])
        .set_file_name(&default_file_name)
        .blocking_save_file()
    {
        Some(path) => path.into_path().map_err(|error| error.to_string())?,
        None => return Ok(None),
    };

    let data = run_python_bridge(
        "template_save",
        json!({ "draft": draft, "save_path": save_path.display().to_string() }),
    )?;
    data.get("path")
        .and_then(|value| value.as_str())
        .map(ToOwned::to_owned)
        .map(Some)
        .ok_or_else(|| "Python ブリッジの保存結果に path がありません。".into())
}

fn safe_file_stem(value: &str) -> String {
    let sanitized: String = value
        .chars()
        .map(|character| match character {
            '\\' | '/' | ':' | '*' | '?' | '"' | '<' | '>' | '|' => '-',
            character if character.is_whitespace() => '_',
            character => character,
        })
        .collect();
    let trimmed = sanitized.trim_matches(['.', ' ', '_', '-']).to_string();
    if trimmed.is_empty() {
        "ocr-template".into()
    } else {
        trimmed
    }
}

#[derive(Deserialize)]
struct BridgeResponse {
    ok: bool,
    data: Option<Value>,
    error: Option<BridgeResponseError>,
}

#[derive(Deserialize)]
struct BridgeResponseError {
    code: String,
    message: String,
    #[allow(dead_code)]
    details: Option<Value>,
}

fn run_python_bridge(command_name: &str, payload: Value) -> Result<Value, String> {
    let project_root = find_project_root()?;
    let script_path = project_root.join("bridge_cli.py");
    let request = json!({ "payload": payload }).to_string();

    for python in ["python", "py"] {
        let mut child = match Command::new(python)
            .arg(&script_path)
            .arg(command_name)
            .current_dir(&project_root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
        {
            Ok(child) => child,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error.to_string()),
        };

        if let Some(mut stdin) = child.stdin.take() {
            stdin
                .write_all(request.as_bytes())
                .map_err(|error| error.to_string())?;
        }

        let output = child.wait_with_output().map_err(|error| error.to_string())?;
        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);

        match serde_json::from_str::<BridgeResponse>(&stdout) {
            Ok(response) if response.ok => {
                return response
                    .data
                    .ok_or_else(|| "Python ブリッジの応答に data がありません。".into());
            }
            Ok(response) => {
                if let Some(error) = response.error {
                    return Err(format!("{}: {}", error.code, error.message));
                }
                return Err("Python ブリッジがエラーを返しました。".into());
            }
            Err(error) => {
                if !output.status.success() {
                    return Err(format!(
                        "Python ブリッジの実行に失敗しました: {}{}",
                        error,
                        if stderr.is_empty() {
                            String::new()
                        } else {
                            format!(" ({})", stderr.trim())
                        }
                    ));
                }
                return Err(format!("Python ブリッジの応答 JSON が不正です: {}", error));
            }
        }
    }

    Err("Python が見つかりません。".into())
}

fn find_project_root() -> Result<PathBuf, String> {
    let current_dir = std::env::current_dir().map_err(|error| error.to_string())?;
    for candidate in current_dir.ancestors() {
        if has_bridge_script(candidate) {
            return Ok(candidate.to_path_buf());
        }
    }

    if let Ok(exe_path) = std::env::current_exe() {
        for candidate in exe_path.ancestors() {
            if has_bridge_script(candidate) {
                return Ok(candidate.to_path_buf());
            }
        }
    }

    Err("bridge_cli.py が見つかりません。".into())
}

fn has_bridge_script(path: &Path) -> bool {
    path.join("bridge_cli.py").is_file()
}
