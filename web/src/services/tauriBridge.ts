import { invoke } from "@tauri-apps/api/core";
import {
  templateDraftToJson,
  templateFileName,
  type TemplateDraft
} from "./templateDocument";

const hasTauri = "__TAURI_INTERNALS__" in window;

export async function openSampleImageBridge(): Promise<string | null> {
  if (!hasTauri) return null;
  return invoke<string>("open_sample_image");
}

export async function loadTemplateBridge(): Promise<string | null> {
  if (!hasTauri) return null;
  return invoke<string>("load_template");
}

function downloadTemplate(draft: TemplateDraft) {
  const blob = new Blob([templateDraftToJson(draft)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = window.document.createElement("a");
  anchor.href = url;
  anchor.download = templateFileName(draft);
  anchor.click();
  URL.revokeObjectURL(url);
}

export async function saveTemplateBridge(draft: TemplateDraft): Promise<string> {
  if (!hasTauri) {
    downloadTemplate(draft);
    return templateFileName(draft);
  }

  return invoke<string>("save_template", { draft: templateDraftToJson(draft) });
}
