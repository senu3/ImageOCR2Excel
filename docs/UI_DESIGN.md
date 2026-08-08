# UI Design Direction

## Product Focus

Image OCR to Excel is a local productivity tool for turning fixed-format image batches into structured Excel rows.

The core workflow should stay narrow:

1. Create a template from one sample image.
2. Verify OCR output against one or more images.
3. Export the batch to Excel with predictable columns.

Features that do not support this workflow should be secondary or hidden behind settings.

## Current UI Stack

The supported desktop UI is the Python/CustomTkinter application:

- CustomTkinter for the desktop shell and workflow UI.
- Python for OCR, template, and Excel processing.

The earlier Tauri + React implementation is archived under
`archive/tauri_prototype/` and is not part of the current migration target.

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

The current Python UI supports:

- Drag to create a region.
- Drag to move a region.
- Handles to resize a region.
- Mouse-wheel scrolling and Ctrl + mouse-wheel zoom.
- Selected region highlighted in orange.
- Nonselected enabled regions highlighted in teal.
- Double-click field-name editing and a context menu for duplicate, edit, and delete.
- Undo for the latest add, delete, move, or resize operation.

## Review Tab

The review tab should make OCR trust visible.

Current controls:

- Current image preview.
- Field-by-field OCR results.
- Editable recognition results.
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

After export, the app shows:

- Success count.
- Error count.
- Link/path to the output file.
- Error details from the `Errors` sheet.
- Per-image queue status and retry of failed images.

CSV export is available as a secondary action when workbook-specific settings and
error sheets are not required.

## Processing Boundary

UI code should not implement OCR, Excel writing, template serialization, or coordinate scaling.

Current processing modules:

- `ImageOCR2Excel/models.py`: template data model.
- `ImageOCR2Excel/ocr/engine.py`: OCR, image preprocessing, postprocessing, coordinate scaling.
- `ImageOCR2Excel/export/`: Excel/CSV writing and error sheet generation.
- `ImageOCR2Excel/templates.py`: template JSON serialization.

If the archived React/Tauri prototype is revived, it should call these capabilities through a small local API or command bridge rather than reimplementing them.

## Evolution Constraints

1. Keep the released Python package UI working while features evolve.
2. Keep OCR, template, and export logic isolated under `ImageOCR2Excel`.
3. Keep game- or document-specific detection outside the generic profile.
4. Do not reactivate the archived Tauri prototype without a concrete product requirement.
5. If it is revived, connect it to the Python engine through a small local API or command boundary.
