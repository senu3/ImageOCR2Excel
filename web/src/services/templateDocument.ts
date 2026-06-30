import type { TemplateEditorState, TemplateField } from "../types";

const TEMPLATE_VERSION = 3;

export type PersistedTemplateField = {
  name: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  enabled: boolean;
  source_width: number;
  source_height: number;
  postprocess: string;
  replace_from: string;
  replace_to: string;
  remove_text: string;
};

export type PersistedTemplateDocument = {
  format: "image-ocr-to-excel-template";
  version: number;
  template_name: string;
  created_at: string;
  lang: string;
  tesseract_path: string;
  sample_image: string;
  sample_image_size: {
    width: number;
    height: number;
  } | null;
  output_settings: {
    sheet_name: string;
    write_mode: "overwrite" | "append";
    start_cell: string;
    include_filename: boolean;
    include_header: boolean;
  };
  fields: PersistedTemplateField[];
};

function templateFieldToDocumentField(field: TemplateField): PersistedTemplateField {
  const x1 = Math.min(field.region.x, field.region.x + field.region.width);
  const y1 = Math.min(field.region.y, field.region.y + field.region.height);
  const x2 = Math.max(field.region.x, field.region.x + field.region.width);
  const y2 = Math.max(field.region.y, field.region.y + field.region.height);

  return {
    name: field.name,
    x1,
    y1,
    x2,
    y2,
    enabled: field.enabled,
    source_width: field.sourceSize.width,
    source_height: field.sourceSize.height,
    postprocess: field.postprocess,
    replace_from: "",
    replace_to: "",
    remove_text: ""
  };
}

export function buildTemplateDocument(state: TemplateEditorState): PersistedTemplateDocument {
  const sampleImage = state.sampleImage;
  const fields = [...state.fields].sort((a, b) => a.order - b.order);

  return {
    format: "image-ocr-to-excel-template",
    version: TEMPLATE_VERSION,
    template_name: sampleImage ? `${sampleImage.name.replace(/\.[^.]+$/, "")}_template` : "ocr-template",
    created_at: new Date().toISOString(),
    lang: "jpn+eng",
    tesseract_path: "",
    sample_image: sampleImage?.name ?? "",
    sample_image_size: sampleImage
      ? {
          width: sampleImage.width,
          height: sampleImage.height
        }
      : null,
    output_settings: {
      sheet_name: "OCR結果",
      write_mode: "overwrite",
      start_cell: "A1",
      include_filename: true,
      include_header: true
    },
    fields: fields.map(templateFieldToDocumentField)
  };
}

export function templateDocumentToJson(document: PersistedTemplateDocument): string {
  return `${JSON.stringify(document, null, 2)}\n`;
}

export function templateFileName(document: PersistedTemplateDocument): string {
  const safeName = document.template_name
    .trim()
    .replace(/[\\/:*?"<>|]+/g, "-")
    .replace(/\s+/g, "_");
  return `${safeName || "ocr-template"}.json`;
}
