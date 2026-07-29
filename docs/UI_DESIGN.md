# UI Design Direction

## Product Focus

Image OCR to Excel is a local productivity tool for turning fixed-format image batches into structured Excel rows.

The core workflow should stay narrow:

1. Create a template from one sample image.
2. Verify OCR output against one or more images.
3. Export the batch to Excel with predictable columns.

Features that do not support this workflow should be secondary or hidden behind settings.

## Recommended UI Stack

The current CustomTkinter UI is serviceable for the Python desktop version, but the long-term UI should move to:

- Tauri + React for the desktop shell and interaction-heavy UI.
- Python as the local OCR/Excel processing engine.

This keeps local file access and Python OCR libraries while allowing a much richer editor experience.

## Primary Layout

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Top bar: sample image, folder, template load/save, settings           │
├───────────────────────────────────────┬──────────────────────────────┤
│ Image workspace                        │ Template / Review / Export   │
│                                       │                              │
│ - zoom / fit / pan                    │ Template tab                 │
│ - draggable regions                   │ - fields list                │
│ - resizable regions                   │ - reorder                    │
│ - selected region handles             │ - postprocess                │
│                                       │                              │
│                                       │ Review tab                   │
│                                       │ - OCR result table           │
│                                       │ - inline correction          │
│                                       │ - per-image navigation       │
│                                       │                              │
│                                       │ Export tab                   │
│                                       │ - output settings            │
│                                       │ - progress                   │
│                                       │ - error list                 │
└───────────────────────────────────────┴──────────────────────────────┘
```

## Template Tab

The template tab owns the field schema.

Required controls:

- Field name.
- Enabled toggle.
- Reorder by drag or up/down commands.
- Region edit.
- OCR postprocess rule.
- Delete.

The field order is the Excel column order. This relationship should be visible in the UI.

## Image Region Editor

Future web UI should support:

- Drag to create a region.
- Drag to move a region.
- Handles to resize a region.
- Zoom to fit, 100%, and mouse wheel zoom.
- Canvas pan.
- Selected region highlighted in orange.
- Nonselected enabled regions highlighted in teal.

The current Python UI only supports drag-create and reselect. This is one of the strongest reasons to move the UI to web technology.

## Review Tab

The review tab should make OCR trust visible.

Required controls:

- Current image preview.
- Field-by-field OCR results.
- Inline corrections.
- Re-run OCR for current image.
- Image navigation.

Future enhancement:

- Batch preview table where rows are images and columns are fields.
- Filter rows with errors or empty values.

## Export Tab

Export settings should remain explicit and conservative:

- Output file.
- Sheet name.
- Overwrite or append.
- Start cell.
- Include filename column.
- Include header row.

After export, the app should show:

- Success count.
- Error count.
- Link/path to the output file.
- Error details from the `Errors` sheet.

## Processing Boundary

UI code should not implement OCR, Excel writing, template serialization, or coordinate scaling.

Current processing modules:

- `ImageOCR2Excel/models.py`: template data model.
- `ImageOCR2Excel/ocr/engine.py`: OCR, image preprocessing, postprocessing, coordinate scaling.
- `ImageOCR2Excel/export/`: Excel/CSV writing and error sheet generation.
- `ImageOCR2Excel/templates.py`: template JSON serialization.

If the React/Tauri prototype is revived, it should call these capabilities through a small local API or command bridge rather than reimplementing them.

## Migration Plan

1. Keep the current Python package UI working.
2. Keep OCR, template, and export logic isolated under `ImageOCR2Excel`.
3. Add a local CLI/API layer only if the Tauri prototype is revived.
4. Connect the React UI to the Python engine through that boundary.
