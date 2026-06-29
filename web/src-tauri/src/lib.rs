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
    Err("Template loading bridge is not connected yet.".into())
}

#[tauri::command]
fn save_template(_template: String) -> Result<(), String> {
    Err("Template saving bridge is not connected yet.".into())
}
