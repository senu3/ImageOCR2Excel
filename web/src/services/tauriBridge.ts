import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import {
  templateDraftToJson,
  templateFileName,
  type TemplateDraft
} from "./templateDocument";
import type { ExcelExportResult, ImageRef, OcrPreviewResult } from "../types";

const hasTauri = "__TAURI_INTERNALS__" in window;

export function imageUrlFromPath(path?: string): string | undefined {
  if (!path || !hasTauri) return undefined;
  return convertFileSrc(path);
}

export async function openSampleImageBridge(): Promise<string | null> {
  if (!hasTauri) return null;
  return invoke<string | null>("open_sample_image");
}

type OpenedImageData = {
  path: string;
  name: string;
  width: number;
  height: number;
};

type OcrPreviewData = {
  image_path: string;
  results: {
    field_id: string;
    name: string;
    raw_text: string;
    value: string;
    error?: string | null;
    warnings?: string[];
  }[];
};

type ExcelExportData = {
  path: string;
  row_count?: number;
  total_images?: number;
  error_count?: number;
  errors?: {
    image: string;
    field: string;
    error: string;
  }[];
};

function ocrPreviewStatus(result: OcrPreviewData["results"][number]): OcrPreviewResult["status"] {
  if (result.error) return "error";
  if (!result.value.trim()) return "empty";
  return "success";
}

export async function openSampleImage(): Promise<ImageRef | null> {
  const raw = await openSampleImageBridge();
  if (!raw) return null;
  const image = JSON.parse(raw) as OpenedImageData;
  return {
    id: image.path,
    name: image.name,
    width: image.width,
    height: image.height,
    path: image.path,
    url: imageUrlFromPath(image.path)
  };
}

export async function loadTemplateBridge(): Promise<string | null> {
  if (!hasTauri) return null;
  return invoke<string | null>("load_template", { path: null });
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

export async function saveTemplateBridge(draft: TemplateDraft): Promise<string | null> {
  if (!hasTauri) {
    downloadTemplate(draft);
    return templateFileName(draft);
  }

  return invoke<string | null>("save_template", { draft: templateDraftToJson(draft) });
}

export async function ocrPreviewBridge(
  imagePath: string,
  draft: TemplateDraft,
  fieldIds?: string[]
): Promise<OcrPreviewResult[]> {
  if (!hasTauri) {
    throw new Error("OCR Preview は Tauri アプリで利用できます。");
  }

  const raw = await invoke<string>("ocr_preview", {
    imagePath,
    draft: templateDraftToJson(draft),
    fieldIds: fieldIds ?? null
  });
  const data = JSON.parse(raw) as OcrPreviewData;
  return data.results.map((result) => ({
    fieldId: result.field_id,
    name: result.name,
    status: ocrPreviewStatus(result),
    rawText: result.raw_text,
    value: result.value,
    error: result.error ?? null,
    warnings: result.warnings ?? []
  }));
}

export async function exportExcelBridge(
  imagePath: string,
  draft: TemplateDraft,
  reviewResults: OcrPreviewResult[]
): Promise<ExcelExportResult | null> {
  if (!hasTauri) {
    throw new Error("Excel Export は Tauri アプリで利用できます。");
  }

  const raw = await invoke<string | null>("export_excel", {
    imagePath,
    draft: templateDraftToJson(draft),
    reviewResults: JSON.stringify(
      reviewResults.map((result) => ({
        field_id: result.fieldId,
        name: result.name,
        value: result.value,
        raw_text: result.rawText,
        error: result.error ?? null
      }))
    )
  });
  if (!raw) return null;

  const data = JSON.parse(raw) as ExcelExportData;
  return {
    path: data.path,
    rowCount: data.row_count ?? data.total_images ?? 0,
    totalImages: data.total_images ?? data.row_count ?? 0,
    errorCount: data.error_count ?? data.errors?.length ?? 0,
    errors: data.errors ?? []
  };
}
