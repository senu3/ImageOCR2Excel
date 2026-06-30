import {
  AlertTriangle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  FileImage,
  FolderOpen,
  GripVertical,
  Maximize,
  MousePointer2,
  Move,
  PanelRight,
  Plus,
  Save,
  Settings,
  Trash2,
  ZoomIn,
  ZoomOut
} from "lucide-react";
import { PointerEvent, useMemo, useRef, useState } from "react";
import { buildTemplateDraft, parseTemplateLoadData, templateDraftToEditorData } from "./services/templateDocument";
import { loadTemplateBridge, saveTemplateBridge } from "./services/tauriBridge";
import { useTemplateEditor } from "./store/templateStore";
import type { PostprocessRule, Region, TemplateField, WorkflowTab } from "./types";

type DragMode =
  | { kind: "create"; origin: { x: number; y: number }; draft: Region }
  | {
      kind: "move" | "resize-se";
      fieldId: string;
      origin: { x: number; y: number };
      startRegion: Region;
    }
  | null;

const postprocessOptions: PostprocessRule[] = ["そのまま", "数字のみ", "数値抽出", "英数字のみ"];

const tabLabels: Record<WorkflowTab, string> = {
  template: "テンプレート",
  review: "確認",
  export: "出力"
};

function pointFromEvent(event: PointerEvent<HTMLElement>, host: HTMLElement, zoom: number) {
  const rect = host.getBoundingClientRect();
  return {
    x: Math.round((event.clientX - rect.left) / zoom),
    y: Math.round((event.clientY - rect.top) / zoom)
  };
}

function normalizeRegion(a: { x: number; y: number }, b: { x: number; y: number }): Region {
  const x = Math.min(a.x, b.x);
  const y = Math.min(a.y, b.y);
  return {
    x,
    y,
    width: Math.abs(a.x - b.x),
    height: Math.abs(a.y - b.y)
  };
}

function clampRegion(region: Region, maxWidth: number, maxHeight: number): Region {
  const width = Math.max(8, Math.min(region.width, maxWidth));
  const height = Math.max(8, Math.min(region.height, maxHeight));
  return {
    x: Math.max(0, Math.min(region.x, maxWidth - width)),
    y: Math.max(0, Math.min(region.y, maxHeight - height)),
    width,
    height
  };
}

function iconButtonLabel(tool: string) {
  return `${tool}を選択`;
}

export default function App() {
  const { state, sortedFields, selectedField, validation, dispatch } = useTemplateEditor();
  const [activeTab, setActiveTab] = useState<WorkflowTab>("template");
  const [status, setStatus] = useState("サンプル画像上で読み取りたい範囲をドラッグしてください。");
  const [drag, setDrag] = useState<DragMode>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);

  const image = state.sampleImage;
  const errorCount = validation.filter((issue) => issue.level === "error").length;
  const warningCount = validation.filter((issue) => issue.level === "warning").length;
  const imageStatus = [
    `サンプル画像: ${image?.name ?? "未選択"}`,
    `画像サイズ: ${image ? `${image.width} x ${image.height}` : "-"}`,
    `項目数: ${sortedFields.filter((field) => field.enabled).length} / ${sortedFields.length}`,
    `選択中: ${selectedField?.name ?? "-"}`
  ].join("  |  ");

  const selectedIssues = useMemo(
    () => validation.filter((issue) => issue.fieldId && issue.fieldId === selectedField?.id),
    [selectedField?.id, validation]
  );

  async function saveTemplate() {
    dispatch({ type: "set-saving", saving: true });
    setStatus("テンプレートを保存しています...");
    try {
      const savedTo = await saveTemplateBridge(buildTemplateDraft(state));
      await new Promise((resolve) => window.setTimeout(resolve, 360));
      dispatch({ type: "mark-saved" });
      setStatus(`テンプレートを保存しました: ${savedTo}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "テンプレート保存でエラーが発生しました。");
    } finally {
      dispatch({ type: "set-saving", saving: false });
    }
  }

  async function loadTemplate() {
    setStatus("テンプレートを読み込んでいます...");
    try {
      const raw = await loadTemplateBridge();
      if (!raw) {
        setStatus("テンプレート読込は Tauri アプリで利用できます。");
        return;
      }
      const loaded = parseTemplateLoadData(raw);
      const editorData = templateDraftToEditorData(loaded.draft);
      dispatch({ type: "load-template", ...editorData });
      setStatus(`テンプレートを読み込みました: ${loaded.path}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "テンプレート読込でエラーが発生しました。");
    }
  }

  function startCreate(event: PointerEvent<HTMLDivElement>) {
    if (!image || state.canvas.tool === "pan" || activeTab !== "template") return;
    const host = canvasRef.current;
    if (!host) return;
    const point = pointFromEvent(event, host, state.canvas.zoom);
    const target = event.target as HTMLElement;
    if (target.closest("[data-region-id]")) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrag({ kind: "create", origin: point, draft: { x: point.x, y: point.y, width: 0, height: 0 } });
  }

  function updateDrag(event: PointerEvent<HTMLDivElement>) {
    if (!drag || !image) return;
    const host = canvasRef.current;
    if (!host) return;
    const point = pointFromEvent(event, host, state.canvas.zoom);

    if (drag.kind === "create") {
      setDrag({ ...drag, draft: clampRegion(normalizeRegion(drag.origin, point), image.width, image.height) });
      return;
    }

    if (drag.kind === "move") {
      dispatch({
        type: "update-region",
        fieldId: drag.fieldId,
        region: clampRegion(
          {
            ...drag.startRegion,
            x: drag.startRegion.x + point.x - drag.origin.x,
            y: drag.startRegion.y + point.y - drag.origin.y
          },
          image.width,
          image.height
        )
      });
      return;
    }

    dispatch({
      type: "update-region",
      fieldId: drag.fieldId,
      region: clampRegion(
        {
          ...drag.startRegion,
          width: drag.startRegion.width + point.x - drag.origin.x,
          height: drag.startRegion.height + point.y - drag.origin.y
        },
        image.width,
        image.height
      )
    });
  }

  function finishDrag(event: PointerEvent<HTMLDivElement>) {
    if (!drag || !image) return;
    event.currentTarget.releasePointerCapture(event.pointerId);
    if (drag.kind === "create" && drag.draft.width >= 8 && drag.draft.height >= 8) {
      dispatch({ type: "create-field", region: drag.draft });
      setStatus("新しい項目を追加しました。右パネルで項目名を設定してください。");
    }
    setDrag(null);
  }

  function startMove(event: PointerEvent<HTMLButtonElement | HTMLDivElement>, field: TemplateField) {
    event.stopPropagation();
    const host = canvasRef.current;
    if (!host || !image) return;
    dispatch({ type: "select-field", fieldId: field.id });
    const point = pointFromEvent(event, host, state.canvas.zoom);
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrag({
      kind: "move",
      fieldId: field.id,
      origin: point,
      startRegion: field.region
    });
  }

  function startResize(event: PointerEvent<HTMLButtonElement>, field: TemplateField) {
    event.stopPropagation();
    const host = canvasRef.current;
    if (!host || !image) return;
    dispatch({ type: "select-field", fieldId: field.id });
    const point = pointFromEvent(event, host, state.canvas.zoom);
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrag({
      kind: "resize-se",
      fieldId: field.id,
      origin: point,
      startRegion: field.region
    });
  }

  return (
    <div className="app-shell">
      <TopToolbar
        dirty={state.dirty}
        saving={state.saving}
        status={status}
        onLoad={loadTemplate}
        onSave={saveTemplate}
      />

      <main className="main-workspace" id="main-content">
        <section className="workspace" aria-label="画像ワークスペース">
          <CanvasToolbar
            tool={state.canvas.tool}
            zoom={state.canvas.zoom}
            onTool={(tool) => dispatch({ type: "set-tool", tool })}
            onZoom={(zoom) => dispatch({ type: "set-zoom", zoom })}
          />
          <div className="canvas-shell">
            <div
              className="image-stage"
              ref={canvasRef}
              style={{
                width: image ? image.width * state.canvas.zoom : 920,
                height: image ? image.height * state.canvas.zoom : 620
              }}
              onPointerDown={startCreate}
              onPointerMove={updateDrag}
              onPointerUp={finishDrag}
            >
              {image ? (
                <SampleDocument zoom={state.canvas.zoom} />
              ) : (
                <div className="canvas-empty">
                  <FileImage size={30} />
                  <strong>サンプル画像を開いてください</strong>
                  <span>テンプレート作成では、読み取りたい場所をドラッグして項目名を付けます。</span>
                </div>
              )}

              {image &&
                sortedFields.map((field) => (
                  <RegionBox
                    key={field.id}
                    field={field}
                    zoom={state.canvas.zoom}
                    selected={field.id === selectedField?.id}
                    onSelect={() => dispatch({ type: "select-field", fieldId: field.id })}
                    onMoveStart={(event) => startMove(event, field)}
                    onResizeStart={(event) => startResize(event, field)}
                  />
                ))}

              {drag?.kind === "create" && <DraftRegion region={drag.draft} zoom={state.canvas.zoom} />}
            </div>
          </div>
        </section>

        <aside className="workflow-panel" aria-label="ワークフローパネル">
          <WorkflowTabs activeTab={activeTab} onChange={setActiveTab} />
          {activeTab === "template" ? (
            <TemplatePanel
              fields={sortedFields}
              selectedField={selectedField}
              selectedIssues={selectedIssues}
              onSelect={(fieldId) => dispatch({ type: "select-field", fieldId })}
              onToggle={(field) =>
                dispatch({ type: "update-field", fieldId: field.id, patch: { enabled: !field.enabled } })
              }
              onRename={(fieldId, name) => dispatch({ type: "update-field", fieldId, patch: { name } })}
              onMove={(fieldId, direction) => dispatch({ type: "move-field", fieldId, direction })}
              onPostprocess={(fieldId, postprocess) =>
                dispatch({ type: "set-postprocess", fieldId, postprocess })
              }
              onDelete={(fieldId) => dispatch({ type: "delete-field", fieldId })}
            />
          ) : (
            <ReservedPanel tab={activeTab} />
          )}
        </aside>
      </main>

      <BottomStatusPanel
        validation={validation}
        status={status}
        imageStatus={imageStatus}
        errorCount={errorCount}
        warningCount={warningCount}
      />
    </div>
  );
}

function TopToolbar({
  dirty,
  saving,
  status,
  onLoad,
  onSave
}: {
  dirty: boolean;
  saving: boolean;
  status: string;
  onLoad: () => void;
  onSave: () => void;
}) {
  return (
    <header className="top-toolbar">
      <div className="toolbar-group app-title">
        <span className="app-mark" aria-hidden="true">
          OCR
        </span>
        <div>
          <strong>Image OCR to Excel</strong>
          <span>{dirty ? "未保存の変更あり" : "テンプレート同期済み"}</span>
        </div>
      </div>

      <nav className="toolbar-group" aria-label="ファイル操作">
        <button className="tool-button primary" type="button">
          <FileImage size={16} />
          サンプル画像
        </button>
        <button className="tool-button" type="button">
          <FolderOpen size={16} />
          画像フォルダ
        </button>
        <button className="tool-button" type="button" onClick={onLoad}>
          <ChevronDown size={16} />
          読込
        </button>
        <button className="tool-button" type="button" onClick={onSave} disabled={saving}>
          <Save size={16} />
          {saving ? "保存中" : "保存"}
        </button>
      </nav>

      <div className="toolbar-status" aria-live="polite">
        {status}
      </div>

      <div className="toolbar-group toolbar-end">
        <button className="icon-button" type="button" aria-label="設定">
          <Settings size={18} />
        </button>
      </div>
    </header>
  );
}

function CanvasToolbar({
  tool,
  zoom,
  onTool,
  onZoom
}: {
  tool: "select" | "create" | "pan";
  zoom: number;
  onTool: (tool: "select" | "create" | "pan") => void;
  onZoom: (zoom: number) => void;
}) {
  return (
    <div className="canvas-toolbar" aria-label="キャンバス操作">
      <div className="segmented-control" role="group" aria-label="編集ツール">
        <button
          className={tool === "select" ? "active" : ""}
          type="button"
          aria-label={iconButtonLabel("選択")}
          onClick={() => onTool("select")}
        >
          <MousePointer2 size={16} />
        </button>
        <button
          className={tool === "create" ? "active" : ""}
          type="button"
          aria-label={iconButtonLabel("範囲作成")}
          onClick={() => onTool("create")}
        >
          <Plus size={16} />
        </button>
        <button
          className={tool === "pan" ? "active" : ""}
          type="button"
          aria-label={iconButtonLabel("パン")}
          onClick={() => onTool("pan")}
        >
          <Move size={16} />
        </button>
      </div>
      <div className="canvas-toolbar-actions">
        <div className="image-navigator" role="group" aria-label="画像選択">
          <button className="icon-button" type="button" aria-label="前の画像">
            <ChevronLeft size={18} />
          </button>
          <span className="image-count">1 / 12</span>
          <button className="icon-button" type="button" aria-label="次の画像">
            <ChevronRight size={18} />
          </button>
        </div>
        <div className="zoom-controls">
          <button className="icon-button" type="button" aria-label="縮小" onClick={() => onZoom(zoom - 0.1)}>
            <ZoomOut size={16} />
          </button>
          <span>{Math.round(zoom * 100)}%</span>
          <button className="icon-button" type="button" aria-label="拡大" onClick={() => onZoom(zoom + 0.1)}>
            <ZoomIn size={16} />
          </button>
          <button className="icon-button" type="button" aria-label="全体表示" onClick={() => onZoom(0.82)}>
            <Maximize size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}

function SampleDocument({ zoom }: { zoom: number }) {
  return (
    <div className="sample-document" style={{ transform: `scale(${zoom})` }} aria-label="サンプル画像">
      <div className="doc-header">
        <span>固定フォーマット サンプル</span>
        <strong>OCR TEMPLATE</strong>
      </div>
      <div className="doc-row wide" />
      <div className="doc-grid">
        <div />
        <div />
        <div />
        <div />
      </div>
      <div className="doc-table">
        {Array.from({ length: 6 }).map((_, index) => (
          <div className="doc-table-row" key={index}>
            <span />
            <span />
            <span />
          </div>
        ))}
      </div>
      <div className="doc-row footer" />
    </div>
  );
}

function RegionBox({
  field,
  zoom,
  selected,
  onSelect,
  onMoveStart,
  onResizeStart
}: {
  field: TemplateField;
  zoom: number;
  selected: boolean;
  onSelect: () => void;
  onMoveStart: (event: PointerEvent<HTMLDivElement>) => void;
  onResizeStart: (event: PointerEvent<HTMLButtonElement>) => void;
}) {
  return (
    <div
      className={`region-box ${selected ? "selected" : ""} ${field.enabled ? "" : "disabled"}`}
      data-region-id={field.id}
      style={{
        left: field.region.x * zoom,
        top: field.region.y * zoom,
        width: field.region.width * zoom,
        height: field.region.height * zoom
      }}
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onPointerDown={onMoveStart}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") onSelect();
      }}
      aria-label={`${field.name} の読み取り範囲`}
    >
      <span className="region-label">
        #{field.order} {field.name}
      </span>
      {selected && (
        <button
          className="resize-handle"
          type="button"
          aria-label={`${field.name} の範囲をリサイズ`}
          onPointerDown={onResizeStart}
        />
      )}
    </div>
  );
}

function DraftRegion({ region, zoom }: { region: Region; zoom: number }) {
  return (
    <div
      className="draft-region"
      style={{
        left: region.x * zoom,
        top: region.y * zoom,
        width: region.width * zoom,
        height: region.height * zoom
      }}
    />
  );
}

function WorkflowTabs({
  activeTab,
  onChange
}: {
  activeTab: WorkflowTab;
  onChange: (tab: WorkflowTab) => void;
}) {
  return (
    <div className="workflow-tabs" role="tablist" aria-label="作業ステップ">
      {(Object.keys(tabLabels) as WorkflowTab[]).map((tab) => (
        <button
          key={tab}
          type="button"
          role="tab"
          aria-selected={tab === activeTab}
          className={tab === activeTab ? "active" : ""}
          onClick={() => onChange(tab)}
        >
          {tabLabels[tab]}
        </button>
      ))}
    </div>
  );
}

function TemplatePanel({
  fields,
  selectedField,
  selectedIssues,
  onSelect,
  onToggle,
  onRename,
  onMove,
  onPostprocess,
  onDelete
}: {
  fields: TemplateField[];
  selectedField: TemplateField | null;
  selectedIssues: { id: string; message: string }[];
  onSelect: (fieldId: string) => void;
  onToggle: (field: TemplateField) => void;
  onRename: (fieldId: string, name: string) => void;
  onMove: (fieldId: string, direction: -1 | 1) => void;
  onPostprocess: (fieldId: string, postprocess: PostprocessRule) => void;
  onDelete: (fieldId: string) => void;
}) {
  return (
    <div className="template-panel">
      <section className="panel-section summary-section">
        <div>
          <span className="section-label">Template</span>
          <h2>読み取り項目</h2>
        </div>
        <span className="count-pill">{fields.filter((field) => field.enabled).length} / {fields.length}</span>
      </section>

      <section className="field-list" aria-label="Excel列順">
        {fields.length === 0 ? (
          <div className="empty-block">
            <PanelRight size={22} />
            <strong>項目がありません</strong>
            <span>画像上で範囲をドラッグして、Excel列になる項目を作成します。</span>
          </div>
        ) : (
          fields.map((field) => (
            <FieldRow
              key={field.id}
              field={field}
              selected={field.id === selectedField?.id}
              onSelect={() => onSelect(field.id)}
              onToggle={() => onToggle(field)}
              onMove={onMove}
            />
          ))
        )}
      </section>

      <SelectedFieldInspector
        field={selectedField}
        issues={selectedIssues}
        onRename={onRename}
        onPostprocess={onPostprocess}
        onDelete={onDelete}
      />
    </div>
  );
}

function FieldRow({
  field,
  selected,
  onSelect,
  onToggle,
  onMove
}: {
  field: TemplateField;
  selected: boolean;
  onSelect: () => void;
  onToggle: () => void;
  onMove: (fieldId: string, direction: -1 | 1) => void;
}) {
  return (
    <div className={`field-row ${selected ? "selected" : ""}`} onClick={onSelect}>
      <GripVertical size={16} className="drag-glyph" aria-hidden="true" />
      <span className="order-chip">#{field.order}</span>
      <button
        className={`toggle ${field.enabled ? "on" : ""}`}
        type="button"
        aria-label={`${field.name}を${field.enabled ? "無効" : "有効"}にする`}
        aria-pressed={field.enabled}
        onClick={(event) => {
          event.stopPropagation();
          onToggle();
        }}
      />
      <div className="field-row-main">
        <strong>{field.name}</strong>
        <span>
          {field.region.width} x {field.region.height}px / {field.postprocess}
        </span>
      </div>
      <div className="row-actions">
        <button type="button" className="icon-button small" aria-label="上へ移動" onClick={(event) => {
          event.stopPropagation();
          onMove(field.id, -1);
        }}>
          <ChevronLeft size={14} />
        </button>
        <button type="button" className="icon-button small" aria-label="下へ移動" onClick={(event) => {
          event.stopPropagation();
          onMove(field.id, 1);
        }}>
          <ChevronRight size={14} />
        </button>
      </div>
    </div>
  );
}

function SelectedFieldInspector({
  field,
  issues,
  onRename,
  onPostprocess,
  onDelete
}: {
  field: TemplateField | null;
  issues: { id: string; message: string }[];
  onRename: (fieldId: string, name: string) => void;
  onPostprocess: (fieldId: string, postprocess: PostprocessRule) => void;
  onDelete: (fieldId: string) => void;
}) {
  if (!field) {
    return (
      <section className="inspector">
        <div className="empty-block compact">
          <MousePointer2 size={20} />
          <strong>項目を選択</strong>
          <span>キャンバスか一覧から編集する項目を選んでください。</span>
        </div>
      </section>
    );
  }

  return (
    <section className="inspector" aria-label="選択中フィールド">
      <div className="inspector-heading">
        <div>
          <span className="section-label">Selected</span>
          <h2>#{field.order} {field.name}</h2>
        </div>
        <button className="icon-button danger" type="button" aria-label={`${field.name}を削除`} onClick={() => onDelete(field.id)}>
          <Trash2 size={16} />
        </button>
      </div>

      <label className="form-field">
        <span>項目名</span>
        <input value={field.name} onChange={(event) => onRename(field.id, event.target.value)} />
      </label>

      <label className="form-field">
        <span>後処理</span>
        <select
          value={field.postprocess}
          onChange={(event) => onPostprocess(field.id, event.target.value as PostprocessRule)}
        >
          {postprocessOptions.map((option) => (
            <option key={option}>{option}</option>
          ))}
        </select>
      </label>

      <div className="region-metrics" aria-label="読み取り範囲">
        <span>X {field.region.x}</span>
        <span>Y {field.region.y}</span>
        <span>W {field.region.width}</span>
        <span>H {field.region.height}</span>
      </div>

      {issues.length > 0 && (
        <div className="inline-issues">
          {issues.map((issue) => (
            <p key={issue.id}>
              <AlertTriangle size={14} />
              {issue.message}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}

function ReservedPanel({ tab }: { tab: Exclude<WorkflowTab, "template"> }) {
  const content =
    tab === "review"
      ? {
          title: "OCR確認は次フェーズ",
          body: "Templateで確定した項目を、画像ごとのOCR結果とinline correctionへ接続します。"
        }
      : {
          title: "Excel出力は次フェーズ",
          body: "出力ファイル、シート名、開始セル、追記/上書き、実行結果をここへ集約します。"
        };
  return (
    <div className="reserved-panel">
      <ChevronsUpDown size={24} />
      <h2>{content.title}</h2>
      <p>{content.body}</p>
    </div>
  );
}

function BottomStatusPanel({
  validation,
  status,
  imageStatus,
  errorCount,
  warningCount,
}: {
  validation: { id: string; level: "warning" | "error"; message: string }[];
  status: string;
  imageStatus: string;
  errorCount: number;
  warningCount: number;
}) {
  return (
    <footer className="bottom-status">
      <section className="state-readout" aria-label="画像情報">
        <span className="footer-label">画像情報</span>
        <p aria-live="polite">{imageStatus}</p>
      </section>
      <section className="validation-summary" aria-label="検証">
        <span className="footer-label">検証</span>
        <div className="validation-counts">
          <span className={errorCount > 0 ? "bad" : ""}>エラー {errorCount}</span>
          <span className={warningCount > 0 ? "warn" : ""}>警告 {warningCount}</span>
        </div>
        <p aria-live="polite">{validation[0]?.message ?? status}</p>
      </section>
    </footer>
  );
}
