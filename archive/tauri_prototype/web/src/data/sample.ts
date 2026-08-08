import type { ImageRef, TemplateField } from "../types";

export const sampleImage: ImageRef = {
  id: "sample-001",
  name: "sample_invoice_001.png",
  width: 1120,
  height: 760
};

export const initialFields: TemplateField[] = [
  {
    id: "field-name",
    name: "名前",
    enabled: true,
    order: 1,
    region: { x: 92, y: 118, width: 360, height: 58 },
    sourceSize: { width: 1120, height: 760 },
    postprocess: "そのまま"
  },
  {
    id: "field-value",
    name: "値",
    enabled: true,
    order: 2,
    region: { x: 698, y: 276, width: 208, height: 62 },
    sourceSize: { width: 1120, height: 760 },
    postprocess: "数値抽出"
  },
  {
    id: "field-note",
    name: "備考",
    enabled: true,
    order: 3,
    region: { x: 102, y: 526, width: 708, height: 74 },
    sourceSize: { width: 1120, height: 760 },
    postprocess: "そのまま"
  }
];
