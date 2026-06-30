#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            open_sample_image,
            load_template,
            save_template
        ])
        .run(tauri::generate_context!())
        .expect("error while running Image OCR to Excel");
}

#[tauri::command]
fn open_sample_image() -> Result<String, String> {
    Err("File selection bridge is not connected yet.".into())
}

#[tauri::command]
fn load_template() -> Result<String, String> {
    let path = std::env::current_dir()
        .map_err(|error| error.to_string())?
        .join("ocr-template.json");
    std::fs::read_to_string(path).map_err(|error| error.to_string())
}

#[tauri::command]
fn save_template(template: String) -> Result<String, String> {
    let document: serde_json::Value =
        serde_json::from_str(&template).map_err(|error| error.to_string())?;
    let template_name = document
        .get("template_name")
        .and_then(|value| value.as_str())
        .unwrap_or("ocr-template");
    let file_name = format!("{}.json", safe_file_stem(template_name));
    let path = std::env::current_dir()
        .map_err(|error| error.to_string())?
        .join(file_name);

    std::fs::write(&path, template).map_err(|error| error.to_string())?;
    Ok(path.display().to_string())
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
