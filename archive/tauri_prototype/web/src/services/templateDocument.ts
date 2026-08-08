import type { ImageRef, PostprocessRule, TemplateEditorState, TemplateField } from "../types";

export type TemplateDraftField = {
  id: string;
  name: string;
  enabled: boolean;
  order: number;
  region: {
    x: number;
    y: number;
    width: number;
    height: number;
  };
  sourceSize: {
    width: number;
    height: number;
  };
  postprocess: string;
};

export type TemplateDraft = {
  template_name: string;
  lang: string;
  tesseract_path: string;
  sample_image: string;
  sample_image_name: string;
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
  fields: TemplateDraftField[];
};

export type TemplateLoadData = {
  path: string;
  template: unknown;
  draft: TemplateDraft;
};

const postprocessOptions: PostprocessRule[] = ["そのまま", "数字のみ", "数値抽出", "英数字のみ"];

function templateFieldToDraftField(field: TemplateField): TemplateDraftField {
  return {
    id: field.id,
    name: field.name,
    enabled: field.enabled,
    order: field.order,
    region: field.region,
    sourceSize: field.sourceSize,
    postprocess: field.postprocess
  };
}

export function buildTemplateDraft(state: TemplateEditorState): TemplateDraft {
  const sampleImage = state.sampleImage;
  const fields = [...state.fields].sort((a, b) => a.order - b.order);

  return {
    template_name: sampleImage ? `${sampleImage.name.replace(/\.[^.]+$/, "")}_template` : "ocr-template",
    lang: "jpn+eng",
    tesseract_path: "",
    sample_image: sampleImage?.path ?? sampleImage?.name ?? "",
    sample_image_name: sampleImage?.name ?? "",
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
    fields: fields.map(templateFieldToDraftField)
  };
}

export function templateDraftToJson(draft: TemplateDraft): string {
  return `${JSON.stringify(draft, null, 2)}\n`;
}

export function templateFileName(draft: TemplateDraft): string {
  const safeName = draft.template_name
    .trim()
    .replace(/[\\/:*?"<>|]+/g, "-")
    .replace(/\s+/g, "_");
  return `${safeName || "ocr-template"}.json`;
}

function fileNameFromPath(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path;
}

function postprocessFromDraft(value: string): PostprocessRule {
  return postprocessOptions.includes(value as PostprocessRule) ? (value as PostprocessRule) : "そのまま";
}

export function parseTemplateLoadData(raw: string): TemplateLoadData {
  const data = JSON.parse(raw) as TemplateLoadData;
  if (!data || typeof data !== "object" || !data.draft) {
    throw new Error("テンプレート読込結果の形式が不正です。");
  }
  return data;
}

export function templateDraftToEditorData(draft: TemplateDraft): {
  sampleImage: ImageRef | null;
  fields: TemplateField[];
} {
  const sampleImage =
    draft.sample_image_size && draft.sample_image
      ? {
          id: "loaded-sample-image",
          name: draft.sample_image_name || fileNameFromPath(draft.sample_image),
          width: draft.sample_image_size.width,
          height: draft.sample_image_size.height,
          path: draft.sample_image
        }
      : null;

  const fields = [...draft.fields]
    .sort((a, b) => a.order - b.order)
    .map((field, index) => ({
      id: field.id || `field-${index + 1}`,
      name: field.name,
      enabled: field.enabled,
      order: index + 1,
      region: field.region,
      sourceSize: field.sourceSize,
      postprocess: postprocessFromDraft(field.postprocess)
    }));

  return { sampleImage, fields };
}
