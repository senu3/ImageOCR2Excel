import { invoke } from "@tauri-apps/api/core";
import {
  templateDocumentToJson,
  templateFileName,
  type PersistedTemplateDocument
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

function downloadTemplate(document: PersistedTemplateDocument) {
  const blob = new Blob([templateDocumentToJson(document)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = window.document.createElement("a");
  anchor.href = url;
  anchor.download = templateFileName(document);
  anchor.click();
  URL.revokeObjectURL(url);
}

export async function saveTemplateBridge(document: PersistedTemplateDocument): Promise<string> {
  if (!hasTauri) {
    downloadTemplate(document);
    return templateFileName(document);
  }

  return invoke<string>("save_template", { template: templateDocumentToJson(document) });
}
