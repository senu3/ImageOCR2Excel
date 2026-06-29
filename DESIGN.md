# Design

## System

Image OCR to Excel is a dense product UI for a local desktop workflow. The interface should feel like a focused workbench: a large image workspace, a persistent right-side task panel, and explicit export controls.

The current implementation is CustomTkinter. The long-term target described in `docs/UI_DESIGN.md` is Tauri + React for the shell and interaction-heavy UI, with Python remaining the OCR and Excel processing engine.

## Register

product

## Visual Direction

Use a black-based dark mode as the default visual direction. The UI should be quiet, legible, and operational rather than decorative. Purple is an accent for focus, selection, active controls, and primary actions; it should not flood the whole product.

Physical scene: a user is working through image batches on a Windows desktop, comparing OCR regions and output fields for accuracy under normal office lighting. The screen should reduce glare, keep the image canvas prominent, and make mistakes visible before export.

Color strategy: restrained dark product UI with one functional purple accent, plus semantic colors for warning, danger, success, and OCR region state.

## Color

Future dark theme tokens should use OKLCH.

```css
:root {
  --color-bg: oklch(0.145 0.018 270);
  --color-surface: oklch(0.19 0.018 270);
  --color-surface-raised: oklch(0.235 0.02 270);
  --color-border: oklch(0.33 0.025 270);
  --color-text: oklch(0.93 0.01 270);
  --color-muted: oklch(0.72 0.018 270);
  --color-accent: oklch(0.68 0.2 300);
  --color-accent-hover: oklch(0.74 0.19 300);
  --color-accent-subtle: oklch(0.27 0.07 300);
  --color-region-enabled: oklch(0.74 0.14 190);
  --color-region-selected: oklch(0.76 0.17 55);
  --color-danger: oklch(0.67 0.2 25);
  --color-warning: oklch(0.78 0.16 75);
  --color-success: oklch(0.72 0.14 150);
}
```

Current CustomTkinter colors are light-shell with a dark canvas:

```text
BG #f6f8fb
Surface #ffffff
Surface alt #f8fafc
Text #12323a
Muted #64748b
Primary teal #0d9488
CTA orange #f97316
Canvas bg #111827
Canvas panel #1f2937
```

When migrating the UI, preserve the functional distinction already present in the code: enabled regions use teal, selected regions use orange, and primary workflow controls have one consistent accent. If adopting purple as the accent, reserve orange for selected OCR regions or warnings so selection stays obvious over image content.

## Typography

Use one practical sans-serif family. For the current Japanese desktop UI, `Meiryo` is acceptable. For the future React/Tauri UI, prefer a system stack that handles Japanese text cleanly:

```css
font-family: system-ui, "Yu Gothic UI", "Meiryo", sans-serif;
```

Use a compact product scale:

- App title: 16-18px, bold.
- Section heading: 14-15px, bold.
- Body and controls: 13-14px.
- Metadata and helper text: 12-13px, with AA contrast.
- Canvas labels: 11-12px, bold, high contrast on a solid label background.

Avoid display typography, fluid hero sizing, wide-tracked labels, and marketing-style section titles.

## Layout

Primary structure:

- Top/toolbar area: sample image, image folder, template load/save, settings, image navigation.
- Main workspace: image canvas with zoom, pan, region drawing, region handles, and selected-region emphasis.
- Right panel: fixed-width workflow panel with Template, Review, and Export tabs.

The right panel should remain dense and predictable. Keep a clear relationship between field order and Excel column order. Export settings should stay explicit and visible before the final action.

Recommended desktop proportions:

- Canvas workspace: flexible, minimum 60% of available width.
- Side panel: 380-440px.
- Toolbar: 44-52px.
- Status bar: 30-36px.
- Card/panel radius: 6-8px.

Do not nest cards. Use panels for functional groups only: field list, OCR results, export settings, progress/errors.

## Components

Core components:

- Image canvas with region overlays.
- Region label with field order and field name.
- Field row with enabled state, order, name, postprocess rule, and source rectangle.
- Segmented workflow tabs: Template, Review, Export.
- OCR result row with editable value.
- Export settings form.
- Progress and error summary.
- Settings dialog for OCR language and Tesseract path.

Every interactive component needs default, hover, focus, active, disabled, and error states. Focus states should be visible in purple or a high-contrast outline and must not rely on color alone.

## Interaction

Template creation should favor direct manipulation:

- Drag to create a region.
- Select a region from canvas or field list.
- Move and resize regions in the future web UI.
- Show enabled regions in teal and selected regions in orange.
- Keep field order visible because it determines Excel column order.

Review should make OCR trust visible:

- Show current image context.
- Present field-by-field OCR results.
- Allow inline correction.
- Support re-running OCR for the current image.
- Preserve image navigation and count.

Export should be conservative:

- Show output file, sheet name, write mode, start cell, filename column, and header row.
- Show success count, error count, output path, and details from the Errors sheet after export.

## Motion

Motion is functional only. Use 150-200ms transitions for tab changes, focus rings, hover feedback, progress updates, and panel reveals. Avoid decorative page-load choreography.

Reduced motion should remove nonessential transitions and preserve immediate state feedback.

## Accessibility

Target WCAG AA contrast. In dark mode, avoid muted gray text below readable contrast. Use labels, icons, borders, or patterns in addition to color for selected, disabled, warning, and error states.

Keyboard requirements:

- Existing shortcuts remain supported: previous/next image, save/load template, export.
- Tab order follows the workflow from toolbar to canvas controls to side panel.
- Canvas region actions need keyboard-accessible alternatives in the future web UI.

## Implementation Notes

UI code should not implement OCR, Excel writing, template serialization, or coordinate scaling. Keep these boundaries:

- `ocr_models.py`: template data model.
- `ocr_engine.py`: OCR, preprocessing, postprocessing, coordinate scaling.
- `excel_exporter.py`: Excel writing and error sheet generation.
- `template_store.py`: template JSON serialization.
- `image_ocr_excel_app.py`: current CustomTkinter UI.

For the future Tauri + React version, call Python capabilities through a small local API or command bridge rather than duplicating processing logic in the frontend.
