import { useMemo, useReducer } from "react";
import { initialFields, sampleImage } from "../data/sample";
import type {
  CanvasTool,
  ImageRef,
  PostprocessRule,
  Region,
  TemplateEditorState,
  TemplateField,
  ValidationIssue
} from "../types";

type Action =
  | { type: "select-field"; fieldId: string | null }
  | { type: "set-tool"; tool: CanvasTool }
  | { type: "set-zoom"; zoom: number }
  | { type: "set-sample-image"; sampleImage: ImageRef }
  | { type: "update-field"; fieldId: string; patch: Partial<TemplateField> }
  | { type: "update-region"; fieldId: string; region: Region }
  | { type: "create-field"; region: Region }
  | { type: "delete-field"; fieldId: string }
  | { type: "move-field"; fieldId: string; direction: -1 | 1 }
  | { type: "set-postprocess"; fieldId: string; postprocess: PostprocessRule }
  | { type: "set-saving"; saving: boolean }
  | { type: "mark-saved" }
  | { type: "load-template"; sampleImage: ImageRef | null; fields: TemplateField[] };

const initialState: TemplateEditorState = {
  sampleImage,
  fields: initialFields,
  selectedFieldId: initialFields[0]?.id ?? null,
  canvas: {
    zoom: 0.82,
    pan: { x: 0, y: 0 },
    tool: "select",
    interaction: "idle"
  },
  dirty: false,
  saving: false
};

function resequence(fields: TemplateField[]): TemplateField[] {
  return fields.map((field, index) => ({ ...field, order: index + 1 }));
}

function reducer(state: TemplateEditorState, action: Action): TemplateEditorState {
  switch (action.type) {
    case "select-field":
      return { ...state, selectedFieldId: action.fieldId };
    case "set-tool":
      return { ...state, canvas: { ...state.canvas, tool: action.tool } };
    case "set-zoom":
      return {
        ...state,
        canvas: { ...state.canvas, zoom: Math.min(2.4, Math.max(0.32, action.zoom)) }
      };
    case "set-sample-image":
      if (!state.sampleImage?.path) {
        return {
          ...state,
          dirty: true,
          fields: [],
          selectedFieldId: null,
          sampleImage: action.sampleImage
        };
      }
      return {
        ...state,
        dirty: true,
        sampleImage: action.sampleImage
      };
    case "update-field":
      return {
        ...state,
        dirty: true,
        fields: state.fields.map((field) =>
          field.id === action.fieldId ? { ...field, ...action.patch } : field
        )
      };
    case "update-region":
      return {
        ...state,
        dirty: true,
        fields: state.fields.map((field) =>
          field.id === action.fieldId ? { ...field, region: action.region } : field
        )
      };
    case "create-field": {
      const id = `field-${Date.now()}`;
      const nextField: TemplateField = {
        id,
        name: `項目${state.fields.length + 1}`,
        enabled: true,
        order: state.fields.length + 1,
        region: action.region,
        sourceSize: {
          width: state.sampleImage?.width ?? 0,
          height: state.sampleImage?.height ?? 0
        },
        postprocess: "そのまま"
      };
      return {
        ...state,
        dirty: true,
        fields: [...state.fields, nextField],
        selectedFieldId: id,
        canvas: { ...state.canvas, tool: "select" }
      };
    }
    case "delete-field": {
      const fields = resequence(state.fields.filter((field) => field.id !== action.fieldId));
      return {
        ...state,
        dirty: true,
        fields,
        selectedFieldId: fields[0]?.id ?? null
      };
    }
    case "move-field": {
      const index = state.fields.findIndex((field) => field.id === action.fieldId);
      const nextIndex = index + action.direction;
      if (index < 0 || nextIndex < 0 || nextIndex >= state.fields.length) return state;
      const fields = [...state.fields];
      const [field] = fields.splice(index, 1);
      fields.splice(nextIndex, 0, field);
      return { ...state, dirty: true, fields: resequence(fields) };
    }
    case "set-postprocess":
      return {
        ...state,
        dirty: true,
        fields: state.fields.map((field) =>
          field.id === action.fieldId ? { ...field, postprocess: action.postprocess } : field
        )
      };
    case "set-saving":
      return { ...state, saving: action.saving };
    case "mark-saved":
      return { ...state, dirty: false };
    case "load-template":
      return {
        ...state,
        sampleImage: action.sampleImage,
        fields: action.fields,
        selectedFieldId: action.fields[0]?.id ?? null,
        dirty: false,
        saving: false
      };
    default:
      return state;
  }
}

function validate(state: TemplateEditorState): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  const enabledFields = state.fields.filter((field) => field.enabled);
  const names = new Map<string, string>();

  if (state.fields.length === 0) {
    issues.push({
      id: "no-fields",
      level: "warning",
      message: "読み取り項目がありません。画像上で範囲をドラッグしてください。"
    });
  }

  for (const field of state.fields) {
    const trimmed = field.name.trim();
    if (!trimmed) {
      issues.push({
        id: `${field.id}-empty-name`,
        fieldId: field.id,
        level: "error",
        message: "項目名が未入力です。"
      });
    }
    if (trimmed && names.has(trimmed)) {
      issues.push({
        id: `${field.id}-duplicate-name`,
        fieldId: field.id,
        level: "error",
        message: `項目名「${trimmed}」が重複しています。`
      });
    }
    names.set(trimmed, field.id);

    if (field.region.width < 8 || field.region.height < 8) {
      issues.push({
        id: `${field.id}-tiny-region`,
        fieldId: field.id,
        level: "error",
        message: `「${field.name}」の範囲が小さすぎます。`
      });
    }
    if (
      state.sampleImage &&
      (field.region.x < 0 ||
        field.region.y < 0 ||
        field.region.x + field.region.width > state.sampleImage.width ||
        field.region.y + field.region.height > state.sampleImage.height)
    ) {
      issues.push({
        id: `${field.id}-outside-image`,
        fieldId: field.id,
        level: "error",
        message: `「${field.name}」の範囲が画像外にはみ出しています。`
      });
    }
  }

  if (enabledFields.length === 0 && state.fields.length > 0) {
    issues.push({
      id: "no-enabled-fields",
      level: "warning",
      message: "有効な項目がありません。Excel出力には少なくとも1項目が必要です。"
    });
  }

  if (state.dirty) {
    issues.push({
      id: "dirty",
      level: "warning",
      message: "テンプレートに未保存の変更があります。"
    });
  }

  return issues;
}

export function useTemplateEditor() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const sortedFields = useMemo(
    () => [...state.fields].sort((a, b) => a.order - b.order),
    [state.fields]
  );
  const selectedField =
    sortedFields.find((field) => field.id === state.selectedFieldId) ?? sortedFields[0] ?? null;
  const validation = useMemo(() => validate(state), [state]);

  return {
    state,
    sortedFields,
    selectedField,
    validation,
    dispatch
  };
}
