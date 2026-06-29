import { invoke } from "@tauri-apps/api/core";
import type { TemplateEditorState } from "../types";

const hasTauri = "__TAURI_INTERNALS__" in window;

export async function openSampleImageBridge(): Promise<string | null> {
  if (!hasTauri) return null;
  return invoke<string>("open_sample_image");
}

export async function loadTemplateBridge(): Promise<string | null> {
  if (!hasTauri) return null;
  return invoke<string>("load_template");
}

export async function saveTemplateBridge(template: TemplateEditorState): Promise<void> {
  if (!hasTauri) return;
  await invoke("save_template", { template: JSON.stringify(template) });
}
