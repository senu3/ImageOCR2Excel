export type WorkflowTab = "template" | "review" | "export";

export type CanvasTool = "select" | "create" | "pan";
export type CanvasInteraction = "idle" | "creating" | "moving" | "resizing";

export type PostprocessRule =
  | "そのまま"
  | "数字のみ"
  | "数値抽出"
  | "英数字のみ";

export type Region = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type ImageRef = {
  id: string;
  name: string;
  width: number;
  height: number;
  path?: string;
  url?: string;
};

export type OcrPreviewResult = {
  fieldId: string;
  name: string;
  rawText: string;
  value: string;
  error?: string | null;
  warnings: string[];
};

export type TemplateField = {
  id: string;
  name: string;
  enabled: boolean;
  order: number;
  region: Region;
  sourceSize: {
    width: number;
    height: number;
  };
  postprocess: PostprocessRule;
};

export type ValidationIssue = {
  id: string;
  fieldId?: string;
  level: "warning" | "error";
  message: string;
};

export type TemplateEditorState = {
  sampleImage: ImageRef | null;
  fields: TemplateField[];
  selectedFieldId: string | null;
  canvas: {
    zoom: number;
    pan: { x: number; y: number };
    tool: CanvasTool;
    interaction: CanvasInteraction;
  };
  dirty: boolean;
  saving: boolean;
};
