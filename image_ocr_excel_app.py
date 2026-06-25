from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import BOTH, BOTTOM, HORIZONTAL, LEFT, RIGHT, TOP, VERTICAL, BooleanVar, Canvas, Scrollbar, StringVar, filedialog, messagebox, simpledialog

import customtkinter as ctk
from openpyxl import Workbook
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageTk

try:
    import pytesseract
except ImportError:
    pytesseract = None


APP_TITLE = "Image OCR to Excel"
DEFAULT_LANG = "jpn+eng"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
TEMPLATE_VERSION = 2

UI_FONT_FAMILY = "Meiryo"
UI_FONT = (UI_FONT_FAMILY, 13)
UI_FONT_SMALL = (UI_FONT_FAMILY, 12)
UI_FONT_BOLD = (UI_FONT_FAMILY, 14, "bold")
UI_FONT_TITLE = (UI_FONT_FAMILY, 16, "bold")
UI_FONT_CANVAS = (UI_FONT_FAMILY, 11, "bold")

COLOR_BG = "#f6f8fb"
COLOR_SURFACE = "#ffffff"
COLOR_SURFACE_ALT = "#f8fafc"
COLOR_BORDER = "#d9e2ec"
COLOR_TEXT = "#12323a"
COLOR_MUTED = "#64748b"
COLOR_PRIMARY = "#0d9488"
COLOR_PRIMARY_HOVER = "#0f766e"
COLOR_SECONDARY = "#334155"
COLOR_SECONDARY_HOVER = "#1f2937"
COLOR_DANGER = "#dc2626"
COLOR_DANGER_HOVER = "#b91c1c"
COLOR_CTA = "#f97316"
COLOR_CTA_HOVER = "#ea580c"
COLOR_CANVAS_BG = "#111827"
COLOR_CANVAS_PANEL = "#1f2937"
COLOR_CANVAS_TOOLBAR = "#0f172a"


@dataclass
class TemplateField:
    name: str
    x1: int
    y1: int
    x2: int
    y2: int
    enabled: bool = True

    def normalized(self) -> "TemplateField":
        x1, x2 = sorted((self.x1, self.x2))
        y1, y2 = sorted((self.y1, self.y2))
        return TemplateField(self.name, x1, y1, x2, y2, self.enabled)


class ImageOcrExcelApp:
    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1220x800")
        self.root.minsize(1040, 680)
        self.root.option_add("*Font", f"{UI_FONT_FAMILY} 10")

        self.image_path: Path | None = None
        self.image_files: list[Path] = []
        self.current_image_index = -1
        self.source_folder: Path | None = None
        self.output_path: Path | None = None
        self.template_path: Path | None = None

        self.original_image: Image.Image | None = None
        self.preview_image: ImageTk.PhotoImage | None = None
        self.image_item: int | None = None
        self.zoom = 1.0

        self.fields: list[TemplateField] = []
        self.current_results: list[str] = []
        self.result_vars: list[StringVar] = []
        self.field_check_vars: list[BooleanVar] = []
        self.selected_index: int | None = None
        self.reselect_index: int | None = None

        self.drag_start: tuple[int, int] | None = None
        self.drag_rect: int | None = None
        self.canvas_rects: dict[int, int] = {}
        self.canvas_labels: dict[int, int] = {}

        self.mode_var = StringVar(value="テンプレート")
        self.image_var = StringVar(value="画像未選択")
        self.folder_var = StringVar(value="フォルダ未選択")
        self.output_var = StringVar(value="出力先未選択")
        self.template_var = StringVar(value="テンプレート未保存")
        self.image_count_var = StringVar(value="0 / 0")
        self.field_count_var = StringVar(value="0 項目")
        self.status_var = StringVar(value="サンプル画像を開き、読み取りたい範囲をドラッグしてください。")
        self.progress_var = StringVar(value="")
        self.lang_var = StringVar(value=DEFAULT_LANG)
        self.lang_display_var = StringVar(value=self._lang_display(DEFAULT_LANG))
        self.tesseract_var = StringVar(value=self._detect_tesseract())

        self.side_body: ctk.CTkFrame | None = None
        self.canvas: Canvas

        self._build_ui()
        self._bind_shortcuts()

    def _build_ui(self) -> None:
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.root.configure(fg_color=COLOR_BG)

        main = ctk.CTkFrame(self.root, corner_radius=0, fg_color="transparent")
        main.pack(side=TOP, fill=BOTH, expand=True)

        canvas_area = ctk.CTkFrame(main, corner_radius=8, fg_color=COLOR_CANVAS_PANEL, border_width=1, border_color="#243246")
        canvas_area.pack(side=LEFT, fill=BOTH, expand=True, padx=(12, 0), pady=12)

        toolbar = ctk.CTkFrame(canvas_area, height=50, corner_radius=0, fg_color=COLOR_CANVAS_TOOLBAR)
        toolbar.pack(side=TOP, fill="x")
        toolbar.pack_propagate(False)
        self._toolbar_button(toolbar, "サンプル画像", self.open_sample_image, COLOR_PRIMARY, COLOR_PRIMARY_HOVER, 104).pack(side=LEFT, padx=(12, 4), pady=10)
        self._toolbar_button(toolbar, "画像フォルダ", self.open_image_folder, COLOR_SECONDARY, COLOR_SECONDARY_HOVER, 104).pack(side=LEFT, padx=4, pady=10)
        ctk.CTkFrame(toolbar, width=1, height=22, fg_color="#334155").pack(side=LEFT, padx=8, pady=14)
        self._toolbar_button(toolbar, "読込", self.load_template, COLOR_SECONDARY, COLOR_SECONDARY_HOVER, 62).pack(side=LEFT, padx=4, pady=10)
        self._toolbar_button(toolbar, "保存", self.save_template, COLOR_SECONDARY, COLOR_SECONDARY_HOVER, 62).pack(side=LEFT, padx=4, pady=10)
        self._toolbar_button(toolbar, "設定", self.open_settings_modal, COLOR_SECONDARY, COLOR_SECONDARY_HOVER, 62).pack(side=LEFT, padx=4, pady=10)
        ctk.CTkLabel(toolbar, textvariable=self.image_var, font=UI_FONT_SMALL, text_color="#dbeafe", anchor="w").pack(side=LEFT, fill="x", expand=True, padx=(12, 12))
        self._toolbar_button(toolbar, ">", self.next_image, "#1e293b", "#334155", 34).pack(side=RIGHT, padx=(4, 12), pady=10)
        ctk.CTkLabel(toolbar, textvariable=self.image_count_var, width=72, anchor="center", font=UI_FONT_SMALL, text_color="#cbd5e1").pack(side=RIGHT)
        self._toolbar_button(toolbar, "<", self.previous_image, "#1e293b", "#334155", 34).pack(side=RIGHT, padx=4, pady=10)

        canvas_frame = ctk.CTkFrame(canvas_area, corner_radius=0, fg_color=COLOR_CANVAS_PANEL)
        canvas_frame.pack(side=TOP, fill=BOTH, expand=True)
        self.canvas = Canvas(canvas_frame, bg=COLOR_CANVAS_BG, highlightthickness=0)
        hbar = Scrollbar(canvas_frame, orient=HORIZONTAL, command=self.canvas.xview)
        vbar = Scrollbar(canvas_frame, orient=VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)

        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<Control-MouseWheel>", self.on_mouse_wheel)

        status_bar = ctk.CTkFrame(canvas_area, height=34, corner_radius=0, fg_color=COLOR_CANVAS_TOOLBAR)
        status_bar.pack(side=BOTTOM, fill="x")
        status_bar.pack_propagate(False)
        ctk.CTkLabel(status_bar, textvariable=self.status_var, anchor="w", font=UI_FONT_SMALL, text_color="#dbeafe").pack(side=LEFT, fill="x", expand=True, padx=12)
        ctk.CTkLabel(status_bar, textvariable=self.progress_var, anchor="e", font=UI_FONT_SMALL, text_color="#cbd5e1").pack(side=RIGHT, padx=12)

        side = ctk.CTkFrame(main, width=390, corner_radius=8, fg_color=COLOR_SURFACE, border_width=1, border_color=COLOR_BORDER)
        side.pack(side=RIGHT, fill="y", padx=(12, 12), pady=12)
        side.pack_propagate(False)

        header = ctk.CTkFrame(side, corner_radius=0, fg_color=COLOR_SURFACE)
        header.pack(side=TOP, fill="x", padx=16, pady=(16, 8))
        ctk.CTkLabel(header, text="定型画像をExcel化", font=UI_FONT_TITLE, text_color=COLOR_TEXT, anchor="w").pack(side=LEFT, fill="x", expand=True)
        ctk.CTkLabel(header, textvariable=self.field_count_var, font=UI_FONT_SMALL, text_color=COLOR_MUTED, anchor="e").pack(side=RIGHT)

        ctk.CTkSegmentedButton(
            side,
            values=["テンプレート", "確認", "出力"],
            variable=self.mode_var,
            command=self._on_mode_change,
            font=UI_FONT_SMALL,
            selected_color=COLOR_PRIMARY,
            selected_hover_color=COLOR_PRIMARY_HOVER,
            unselected_color="#e2e8f0",
            unselected_hover_color="#cbd5e1",
            text_color=COLOR_TEXT,
        ).pack(side=TOP, fill="x", padx=16, pady=(0, 10))

        self.side_body = ctk.CTkFrame(side, corner_radius=0, fg_color=COLOR_SURFACE)
        self.side_body.pack(side=TOP, fill=BOTH, expand=True)
        self._render_side_body()

    def _toolbar_button(self, parent, text: str, command, fg: str, hover: str, width: int) -> ctk.CTkButton:
        return ctk.CTkButton(parent, text=text, command=command, width=width, height=30, font=UI_FONT_SMALL, fg_color=fg, hover_color=hover)

    def _bind_shortcuts(self) -> None:
        self.root.bind_all("<Control-Left>", self.previous_image)
        self.root.bind_all("<Control-Right>", self.next_image)
        self.root.bind_all("<Control-s>", self.save_template)
        self.root.bind_all("<Control-o>", self.load_template)
        self.root.bind_all("<Control-Return>", self.export_to_excel)

    def _on_mode_change(self, _value: str | None = None) -> None:
        self._render_side_body()
        self.redraw()

    def _render_side_body(self) -> None:
        if self.side_body is None:
            return
        for child in self.side_body.winfo_children():
            child.destroy()
        self._sync_counts()
        mode = self.mode_var.get()
        if mode == "テンプレート":
            self._render_template_mode()
        elif mode == "確認":
            self._render_review_mode()
        else:
            self._render_export_mode()

    def _render_template_mode(self) -> None:
        body = self.side_body
        assert body is not None
        self.field_check_vars = []
        self._section_note(body, "1. テンプレート作成", "サンプル画像上で読み取り範囲をドラッグし、項目名を付けます。")
        ctk.CTkLabel(body, textvariable=self.template_var, anchor="w", font=UI_FONT_SMALL, text_color=COLOR_MUTED, wraplength=340).pack(fill="x", padx=16, pady=(0, 8))

        scroller = ctk.CTkScrollableFrame(body, corner_radius=8, fg_color=COLOR_SURFACE_ALT, border_width=1, border_color=COLOR_BORDER)
        scroller.pack(side=TOP, fill=BOTH, expand=True, padx=16, pady=(0, 10))
        if not self.fields:
            self._empty_state(scroller, "項目がありません", "画像上で範囲をドラッグ")
        else:
            for idx, field in enumerate(self.fields):
                self._field_row(scroller, idx)

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkButton(actions, text="項目名", command=self.rename_field, height=34, font=UI_FONT_SMALL, fg_color=COLOR_SECONDARY, hover_color=COLOR_SECONDARY_HOVER).pack(side=LEFT, fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(actions, text="範囲変更", command=self.reselect_field, height=34, font=UI_FONT_SMALL, fg_color=COLOR_SECONDARY, hover_color=COLOR_SECONDARY_HOVER).pack(side=LEFT, fill="x", expand=True, padx=4)
        ctk.CTkButton(actions, text="削除", command=self.delete_field, height=34, font=UI_FONT_SMALL, fg_color=COLOR_DANGER, hover_color=COLOR_DANGER_HOVER).pack(side=LEFT, fill="x", expand=True, padx=(4, 0))

    def _render_review_mode(self) -> None:
        body = self.side_body
        assert body is not None
        self._section_note(body, "2. 読み取り確認", "現在の画像のOCR結果を確認し、必要なら直接修正します。")

        ctk.CTkButton(body, text="現在の画像をOCR", command=self.ocr_current_image, height=36, font=UI_FONT_BOLD, fg_color=COLOR_PRIMARY, hover_color=COLOR_PRIMARY_HOVER).pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(body, textvariable=self.folder_var, anchor="w", font=UI_FONT_SMALL, text_color=COLOR_MUTED, wraplength=340).pack(fill="x", padx=16, pady=(0, 8))

        scroller = ctk.CTkScrollableFrame(body, corner_radius=8, fg_color=COLOR_SURFACE_ALT, border_width=1, border_color=COLOR_BORDER)
        scroller.pack(side=TOP, fill=BOTH, expand=True, padx=16, pady=(0, 12))
        if not self.fields:
            self._empty_state(scroller, "テンプレートがありません", "先に読み取り項目を作成")
            return
        if not self.original_image:
            self._empty_state(scroller, "画像がありません", "サンプル画像またはフォルダを選択")
            return

        self._ensure_current_results()
        self.result_vars = []
        for idx, field in enumerate(self.fields):
            if not field.enabled:
                continue
            row = ctk.CTkFrame(scroller, corner_radius=8, fg_color=COLOR_SURFACE, border_width=1, border_color=COLOR_BORDER)
            row.pack(fill="x", padx=8, pady=5)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=field.name, anchor="w", font=UI_FONT_BOLD, text_color=COLOR_TEXT).grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
            var = StringVar(value=self.current_results[idx])
            var.trace_add("write", lambda *_args, i=idx, v=var: self._set_result(i, v.get()))
            self.result_vars.append(var)
            ctk.CTkEntry(row, textvariable=var, height=32, font=UI_FONT_SMALL, fg_color=COLOR_SURFACE_ALT, border_color=COLOR_BORDER, text_color=COLOR_TEXT).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))

    def _render_export_mode(self) -> None:
        body = self.side_body
        assert body is not None
        self._section_note(body, "3. Excel出力", "フォルダ内の画像を、1画像1行としてExcelへ書き出します。")

        panel = ctk.CTkFrame(body, corner_radius=8, fg_color=COLOR_SURFACE_ALT, border_width=1, border_color=COLOR_BORDER)
        panel.pack(fill="x", padx=16, pady=(0, 10))
        panel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(panel, text="画像フォルダ", anchor="w", font=UI_FONT_SMALL, text_color=COLOR_TEXT).grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 2))
        ctk.CTkLabel(panel, textvariable=self.folder_var, anchor="w", font=UI_FONT_SMALL, text_color=COLOR_MUTED, wraplength=320).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        ctk.CTkButton(panel, text="フォルダ選択", command=self.open_image_folder, height=32, font=UI_FONT_SMALL, fg_color=COLOR_SECONDARY, hover_color=COLOR_SECONDARY_HOVER).grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        ctk.CTkLabel(panel, text="出力ファイル", anchor="w", font=UI_FONT_SMALL, text_color=COLOR_TEXT).grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 2))
        ctk.CTkLabel(panel, textvariable=self.output_var, anchor="w", font=UI_FONT_SMALL, text_color=COLOR_MUTED, wraplength=320).grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 8))
        ctk.CTkButton(panel, text="出力先を選択", command=self.select_output_file, height=32, font=UI_FONT_SMALL, fg_color=COLOR_SECONDARY, hover_color=COLOR_SECONDARY_HOVER).grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 12))

        enabled_count = len(self._enabled_fields())
        image_count = len(self.image_files)
        summary = ctk.CTkFrame(body, corner_radius=8, fg_color=COLOR_SURFACE_ALT, border_width=1, border_color=COLOR_BORDER)
        summary.pack(fill="x", padx=16, pady=(0, 10))
        ctk.CTkLabel(summary, text=f"出力対象: {image_count} 画像 / {enabled_count} 項目", anchor="w", font=UI_FONT_BOLD, text_color=COLOR_TEXT).pack(fill="x", padx=12, pady=(12, 4))
        ctk.CTkLabel(summary, text="列構成: 画像ファイル名 + 有効な項目", anchor="w", font=UI_FONT_SMALL, text_color=COLOR_MUTED).pack(fill="x", padx=12, pady=(0, 12))

        ctk.CTkButton(body, text="Excelへ一括出力", command=self.export_to_excel, height=42, font=UI_FONT_BOLD, fg_color=COLOR_CTA, hover_color=COLOR_CTA_HOVER).pack(side=BOTTOM, fill="x", padx=16, pady=(8, 14))

    def _section_note(self, parent, title: str, note: str) -> None:
        ctk.CTkLabel(parent, text=title, anchor="w", font=UI_FONT_BOLD, text_color=COLOR_TEXT).pack(fill="x", padx=16, pady=(4, 2))
        ctk.CTkLabel(parent, text=note, anchor="w", justify="left", font=UI_FONT_SMALL, text_color=COLOR_MUTED, wraplength=340).pack(fill="x", padx=16, pady=(0, 12))

    def _empty_state(self, parent, title: str, note: str) -> None:
        box = ctk.CTkFrame(parent, fg_color=COLOR_SURFACE_ALT, corner_radius=8)
        box.pack(fill=BOTH, expand=True, padx=12, pady=20)
        ctk.CTkLabel(box, text=title, font=UI_FONT_BOLD, text_color=COLOR_TEXT).pack(pady=(28, 4))
        ctk.CTkLabel(box, text=note, font=UI_FONT_SMALL, text_color=COLOR_MUTED).pack(pady=(0, 28))

    def _field_row(self, parent, idx: int) -> None:
        field = self.fields[idx]
        selected = idx == self.selected_index
        row = ctk.CTkFrame(parent, corner_radius=8, fg_color="#ecfeff" if selected else COLOR_SURFACE, border_width=1, border_color=COLOR_PRIMARY if selected else COLOR_BORDER)
        row.pack(fill="x", padx=8, pady=5)
        row.grid_columnconfigure(2, weight=1)
        var = BooleanVar(value=field.enabled)
        self.field_check_vars.append(var)
        ctk.CTkCheckBox(row, text="", variable=var, width=28, command=lambda i=idx, v=var: self.set_field_enabled(i, v.get())).grid(row=0, column=0, rowspan=2, padx=(8, 0), pady=8)
        ctk.CTkLabel(row, text=self._field_order_label(idx), width=42, height=22, font=UI_FONT_SMALL, fg_color=COLOR_PRIMARY if field.enabled else "#94a3b8", text_color="#ffffff", corner_radius=4).grid(row=0, column=1, sticky="w", padx=(4, 6), pady=(8, 2))
        ctk.CTkLabel(row, text=field.name, anchor="w", font=UI_FONT_BOLD, text_color=COLOR_TEXT if field.enabled else COLOR_MUTED).grid(row=0, column=2, sticky="ew", padx=(0, 8), pady=(8, 2))
        ctk.CTkLabel(row, text=self._field_size_text(field), anchor="w", font=UI_FONT_SMALL, text_color=COLOR_MUTED).grid(row=1, column=1, columnspan=2, sticky="ew", padx=(4, 8), pady=(0, 8))
        for widget in row.winfo_children() + [row]:
            widget.bind("<Button-1>", lambda _event, i=idx: self.select_field(i))

    def open_sample_image(self) -> None:
        file_name = filedialog.askopenfilename(
            title="サンプル画像を選択",
            filetypes=[("Image files", "*.png;*.jpg;*.jpeg;*.bmp;*.tif;*.tiff"), ("All files", "*.*")],
        )
        if not file_name:
            return
        self.source_folder = None
        self.folder_var.set("フォルダ未選択")
        self.image_files = [Path(file_name)]
        self.current_image_index = 0
        self._load_current_image(auto_ocr=False)
        self.mode_var.set("テンプレート")
        self._render_side_body()

    def open_image_folder(self) -> None:
        folder_name = filedialog.askdirectory(title="画像フォルダを選択")
        if not folder_name:
            return
        folder = Path(folder_name)
        files = sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)
        if not files:
            messagebox.showinfo("画像なし", "選択したフォルダ内に対応画像がありません。")
            return
        self.source_folder = folder
        self.folder_var.set(str(folder))
        self.image_files = files
        self.current_image_index = 0
        self._load_current_image(auto_ocr=bool(self.fields))
        if self.mode_var.get() == "テンプレート":
            self.mode_var.set("確認")
        self._render_side_body()

    def previous_image(self, _event=None) -> None:
        if len(self.image_files) <= 1:
            return
        self.current_image_index = (self.current_image_index - 1) % len(self.image_files)
        self._load_current_image(auto_ocr=bool(self.fields))
        self._render_side_body()

    def next_image(self, _event=None) -> None:
        if len(self.image_files) <= 1:
            return
        self.current_image_index = (self.current_image_index + 1) % len(self.image_files)
        self._load_current_image(auto_ocr=bool(self.fields))
        self._render_side_body()

    def _load_current_image(self, auto_ocr: bool) -> None:
        if not (0 <= self.current_image_index < len(self.image_files)):
            return
        self.image_path = self.image_files[self.current_image_index]
        try:
            self.original_image = Image.open(self.image_path).convert("RGB")
        except Exception as exc:
            messagebox.showerror("画像エラー", f"画像を開けませんでした。\n{exc}")
            return
        self.zoom = self._initial_zoom()
        self.current_results = [""] * len(self.fields)
        self.image_var.set(self._image_status_text())
        self.image_count_var.set(f"{self.current_image_index + 1} / {len(self.image_files)}")
        self.selected_index = self.selected_index if self.fields else None
        self.reselect_index = None
        self.redraw()
        if auto_ocr:
            self._ocr_all_current(show_errors=False)
        self.status_var.set("画像を読み込みました。読み取り結果を確認できます。")

    def _image_status_text(self) -> str:
        if not self.image_path:
            return "画像未選択"
        if len(self.image_files) > 1:
            return f"{self.current_image_index + 1}/{len(self.image_files)}: {self.image_path.name}"
        return str(self.image_path)

    def select_output_file(self) -> None:
        file_name = filedialog.asksaveasfilename(
            title="Excel出力先を選択",
            defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx")],
        )
        if not file_name:
            return
        self.output_path = Path(file_name)
        self.output_var.set(str(self.output_path))
        self._render_side_body()

    def save_template(self, _event=None) -> None:
        if not self.fields:
            messagebox.showinfo("項目なし", "保存する読み取り項目がありません。")
            return
        initial = self.template_path.name if self.template_path else "ocr-template.json"
        file_name = filedialog.asksaveasfilename(
            title="テンプレートを保存",
            initialfile=initial,
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not file_name:
            return
        path = Path(file_name)
        data = {
            "version": TEMPLATE_VERSION,
            "lang": self.lang_var.get(),
            "tesseract_path": self.tesseract_var.get(),
            "sample_image": str(self.image_path) if self.image_path else "",
            "fields": [asdict(field.normalized()) for field in self.fields],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.template_path = path
        self.template_var.set(str(path))
        self.status_var.set(f"テンプレートを保存しました: {path}")
        self._render_side_body()

    def load_template(self, _event=None) -> None:
        file_name = filedialog.askopenfilename(
            title="テンプレートを読み込み",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not file_name:
            return
        path = Path(file_name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            fields = [self._field_from_data(item) for item in data.get("fields", [])]
        except Exception as exc:
            messagebox.showerror("テンプレートエラー", f"テンプレートを読み込めませんでした。\n{exc}")
            return
        if not fields:
            messagebox.showerror("テンプレートエラー", "読み取り項目がありません。")
            return
        self.fields = fields
        self.current_results = [""] * len(self.fields)
        self.selected_index = 0
        self.template_path = path
        self.template_var.set(str(path))
        self.lang_var.set(data.get("lang") or DEFAULT_LANG)
        self.lang_display_var.set(self._lang_display(self.lang_var.get()))
        if data.get("tesseract_path"):
            self.tesseract_var.set(data["tesseract_path"])
        sample_raw = data.get("sample_image") or ""
        sample = Path(sample_raw) if sample_raw else None
        if sample and sample.exists() and not self.original_image:
            self.image_files = [sample]
            self.current_image_index = 0
            self._load_current_image(auto_ocr=False)
        self.status_var.set(f"テンプレートを読み込みました: {path}")
        self._render_side_body()
        self.redraw()

    def _field_from_data(self, item: dict) -> TemplateField:
        if "cell" in item:
            return TemplateField(str(item.get("name") or item.get("cell") or "項目"), int(item["x1"]), int(item["y1"]), int(item["x2"]), int(item["y2"]), bool(item.get("enabled", True))).normalized()
        return TemplateField(str(item["name"]), int(item["x1"]), int(item["y1"]), int(item["x2"]), int(item["y2"]), bool(item.get("enabled", True))).normalized()

    def open_settings_modal(self) -> None:
        self.lang_display_var.set(self._lang_display(self.lang_var.get()))
        window = ctk.CTkToplevel(self.root)
        window.title("設定")
        window.geometry("420x300")
        window.resizable(False, False)
        window.transient(self.root)
        window.grab_set()
        window.configure(fg_color=COLOR_BG)

        panel = ctk.CTkFrame(window, corner_radius=8, fg_color=COLOR_SURFACE, border_width=1, border_color=COLOR_BORDER)
        panel.pack(fill=BOTH, expand=True, padx=18, pady=18)
        panel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(panel, text="OCR設定", anchor="w", font=UI_FONT_TITLE, text_color=COLOR_TEXT).grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 14))
        ctk.CTkLabel(panel, text="OCR言語", anchor="w", font=UI_FONT_SMALL, text_color=COLOR_TEXT).grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkComboBox(
            panel,
            variable=self.lang_display_var,
            values=["日本語 + English", "日本語のみ", "English のみ"],
            command=self._sync_lang_from_display,
            height=32,
            font=UI_FONT,
            dropdown_font=UI_FONT,
            fg_color=COLOR_SURFACE_ALT,
            border_color=COLOR_BORDER,
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_PRIMARY_HOVER,
            text_color=COLOR_TEXT,
        ).grid(row=2, column=0, sticky="ew", padx=16)
        ctk.CTkLabel(panel, text="Tesseractパス", anchor="w", font=UI_FONT_SMALL, text_color=COLOR_TEXT).grid(row=3, column=0, sticky="ew", padx=16, pady=(14, 6))
        ctk.CTkEntry(panel, textvariable=self.tesseract_var, height=32, font=UI_FONT, fg_color=COLOR_SURFACE_ALT, border_color=COLOR_BORDER, text_color=COLOR_TEXT).grid(row=4, column=0, sticky="ew", padx=16)
        buttons = ctk.CTkFrame(panel, fg_color="transparent")
        buttons.grid(row=5, column=0, sticky="ew", padx=16, pady=(20, 16))
        buttons.grid_columnconfigure(0, weight=1)
        buttons.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(buttons, text="キャンセル", command=window.destroy, height=34, font=UI_FONT, fg_color=COLOR_SURFACE, hover_color="#e2e8f0", border_width=1, border_color=COLOR_BORDER, text_color=COLOR_TEXT).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(buttons, text="保存", command=lambda: self._apply_settings(window), height=34, font=UI_FONT, fg_color=COLOR_PRIMARY, hover_color=COLOR_PRIMARY_HOVER).grid(row=0, column=1, sticky="ew", padx=(4, 0))

    def _apply_settings(self, window: ctk.CTkToplevel) -> None:
        self._sync_lang_from_display()
        self.status_var.set("OCR設定を更新しました。")
        window.destroy()

    def select_field(self, idx: int) -> None:
        if not (0 <= idx < len(self.fields)):
            return
        self.selected_index = idx
        self._render_side_body()
        self.redraw()

    def set_field_enabled(self, idx: int, enabled: bool) -> None:
        if 0 <= idx < len(self.fields):
            self.fields[idx].enabled = enabled
            self._sync_counts()
            self.redraw()

    def rename_field(self) -> None:
        idx = self._require_field_selection()
        if idx is None:
            return
        name = simpledialog.askstring("項目名", "読み取り項目名を入力してください。", initialvalue=self.fields[idx].name)
        if name is None:
            return
        name = self._unique_field_name(name.strip() or self.fields[idx].name, skip_index=idx)
        self.fields[idx].name = name
        self._render_side_body()
        self.redraw()

    def reselect_field(self) -> None:
        idx = self._require_field_selection()
        if idx is None:
            return
        self.reselect_index = idx
        self.status_var.set(f"{self.fields[idx].name} の範囲を再設定します。画像上で新しい範囲をドラッグしてください。")

    def delete_field(self) -> None:
        idx = self._require_field_selection()
        if idx is None:
            return
        del self.fields[idx]
        if idx < len(self.current_results):
            del self.current_results[idx]
        self.selected_index = min(idx, len(self.fields) - 1) if self.fields else None
        self._render_side_body()
        self.redraw()

    def _require_field_selection(self) -> int | None:
        if self.selected_index is None or not (0 <= self.selected_index < len(self.fields)):
            messagebox.showinfo("項目未選択", "読み取り項目を選択してください。")
            return None
        return self.selected_index

    def redraw(self) -> None:
        self.canvas.delete("all")
        self.canvas_rects.clear()
        self.canvas_labels.clear()
        if not self.original_image:
            self._draw_canvas_empty()
            return
        w, h = self.original_image.size
        scaled = self.original_image.resize((max(1, int(w * self.zoom)), max(1, int(h * self.zoom))), Image.Resampling.LANCZOS)
        self.preview_image = ImageTk.PhotoImage(scaled)
        self.image_item = self.canvas.create_image(0, 0, anchor="nw", image=self.preview_image)
        self.canvas.configure(scrollregion=(0, 0, scaled.width, scaled.height))
        for idx, _field in enumerate(self.fields):
            self._draw_field(idx)

    def _draw_canvas_empty(self) -> None:
        self.canvas.configure(scrollregion=(0, 0, 900, 600))
        self.canvas.create_text(450, 280, text="サンプル画像を開いてください", fill="#cbd5e1", font=UI_FONT_TITLE)
        self.canvas.create_text(450, 312, text="テンプレート作成では、読み取りたい場所をドラッグして項目名を付けます。", fill="#94a3b8", font=UI_FONT_SMALL)

    def _draw_field(self, idx: int) -> None:
        field = self.fields[idx].normalized()
        x1, y1, x2, y2 = [value * self.zoom for value in (field.x1, field.y1, field.x2, field.y2)]
        selected = idx == self.selected_index
        color = COLOR_CTA if selected else ("#2dd4bf" if field.enabled else "#94a3b8")
        label_bg = COLOR_CTA_HOVER if selected else (COLOR_PRIMARY if field.enabled else COLOR_MUTED)
        rect = self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=3 if selected else 2)
        label_text = f"{self._field_order_label(idx)} {field.name}"
        label = self.canvas.create_text(x1 + 10, max(6, y1 - 22), anchor="nw", text=label_text, fill="white", font=UI_FONT_CANVAS)
        label_box = self.canvas.bbox(label)
        if label_box:
            bg = self.canvas.create_rectangle(label_box[0] - 5, label_box[1] - 3, label_box[2] + 5, label_box[3] + 3, fill=label_bg, outline=label_bg)
            self.canvas.tag_lower(bg, label)
        self.canvas_rects[idx] = rect
        self.canvas_labels[idx] = label

    def on_mouse_down(self, event) -> None:
        if not self.original_image:
            return
        self.drag_start = (int(self.canvas.canvasx(event.x) / self.zoom), int(self.canvas.canvasy(event.y) / self.zoom))
        x = self.drag_start[0] * self.zoom
        y = self.drag_start[1] * self.zoom
        self.drag_rect = self.canvas.create_rectangle(x, y, x, y, outline=COLOR_CTA, width=2, dash=(4, 2))

    def on_mouse_drag(self, event) -> None:
        if not self.drag_start or not self.drag_rect:
            return
        x1, y1 = self.drag_start
        x2 = int(self.canvas.canvasx(event.x) / self.zoom)
        y2 = int(self.canvas.canvasy(event.y) / self.zoom)
        self.canvas.coords(self.drag_rect, x1 * self.zoom, y1 * self.zoom, x2 * self.zoom, y2 * self.zoom)

    def on_mouse_up(self, event) -> None:
        if not self.original_image or not self.drag_start:
            return
        x1, y1 = self.drag_start
        x2 = int(self.canvas.canvasx(event.x) / self.zoom)
        y2 = int(self.canvas.canvasy(event.y) / self.zoom)
        self.drag_start = None
        if self.drag_rect:
            self.canvas.delete(self.drag_rect)
            self.drag_rect = None

        w, h = self.original_image.size
        x1, x2 = sorted((max(0, min(w, x1)), max(0, min(w, x2))))
        y1, y2 = sorted((max(0, min(h, y1)), max(0, min(h, y2))))
        if (x2 - x1) < 8 or (y2 - y1) < 8:
            return

        if self.reselect_index is not None:
            idx = self.reselect_index
            self.reselect_index = None
            if not (0 <= idx < len(self.fields)):
                return
            field = self.fields[idx]
            field.x1, field.y1, field.x2, field.y2 = x1, y1, x2, y2
            if idx < len(self.current_results):
                self.current_results[idx] = ""
            self.selected_index = idx
            self.status_var.set(f"{field.name} の範囲を更新しました。")
            self._render_side_body()
            self.redraw()
            return

        name = simpledialog.askstring("項目名", "読み取り項目名を入力してください。", initialvalue=f"項目{len(self.fields) + 1}")
        if name is None:
            return
        name = self._unique_field_name(name.strip() or f"項目{len(self.fields) + 1}")
        self.fields.append(TemplateField(name, x1, y1, x2, y2))
        self.current_results.append("")
        self.selected_index = len(self.fields) - 1
        self.mode_var.set("テンプレート")
        self.status_var.set(f"{name} を追加しました。")
        self._render_side_body()
        self.redraw()

    def on_mouse_wheel(self, event) -> None:
        if not self.original_image:
            return
        ctrl_pressed = bool(event.state & 0x0004)
        if not ctrl_pressed:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            return
        factor = 1.1 if event.delta > 0 else 0.9
        self.zoom = max(0.1, min(4.0, self.zoom * factor))
        self.redraw()

    def ocr_current_image(self) -> None:
        if not self.original_image:
            messagebox.showerror("画像未選択", "先に画像を開いてください。")
            return
        if not self._enabled_fields():
            messagebox.showerror("項目なし", "有効な読み取り項目がありません。")
            return
        if self._ocr_all_current(show_errors=True):
            self.status_var.set("現在の画像をOCRしました。")
            self._render_side_body()

    def _ocr_all_current(self, show_errors: bool) -> bool:
        if not self.original_image:
            return False
        self._ensure_current_results()
        for idx, field in enumerate(self.fields):
            if not field.enabled:
                continue
            text = self._ocr_field(self.original_image, field, show_errors=show_errors)
            if text is None:
                return False
            self.current_results[idx] = text
        return True

    def _ocr_field(self, image: Image.Image, field: TemplateField, show_errors: bool) -> str | None:
        ocr = self._load_tesseract(show_errors=show_errors)
        if ocr is None:
            return None
        region = field.normalized()
        crop = image.crop((region.x1, region.y1, region.x2, region.y2))
        prepared = self._prepare_for_ocr(crop)
        try:
            text = ocr.image_to_string(prepared, lang=self.lang_var.get().strip() or DEFAULT_LANG, config="--oem 3 --psm 6")
        except ocr.TesseractNotFoundError:
            if show_errors:
                messagebox.showerror("OCRエラー", "Tesseractが見つかりません。設定で実行ファイルのパスを指定してください。")
            return None
        except ocr.TesseractError as exc:
            if show_errors:
                messagebox.showerror("OCRエラー", str(exc))
            return None
        return self._clean_text(text)

    def export_to_excel(self, _event=None) -> None:
        fields = self._enabled_fields()
        if not fields:
            messagebox.showerror("項目なし", "有効な読み取り項目がありません。")
            return
        if not self.image_files:
            messagebox.showerror("画像なし", "画像フォルダを選択してください。")
            return
        if not self.output_path:
            self.select_output_file()
            if not self.output_path:
                return
        if self.output_path.exists() and not messagebox.askyesno("上書き確認", f"既存ファイルを上書きします。\n{self.output_path}"):
            return

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "OCR"
        sheet.append(["画像ファイル", *[field.name for field in fields]])

        total = len(self.image_files)
        self.progress_var.set(f"0 / {total}")
        self.status_var.set("Excel出力を開始しました。")
        self.root.update_idletasks()

        for row_index, image_path in enumerate(self.image_files, start=1):
            try:
                image = Image.open(image_path).convert("RGB")
            except Exception as exc:
                messagebox.showerror("画像エラー", f"{image_path.name} を開けませんでした。\n{exc}")
                return
            row = [image_path.name]
            for field in fields:
                text = self._ocr_field(image, field, show_errors=True)
                if text is None:
                    self.progress_var.set("")
                    return
                row.append(text)
            sheet.append(row)
            self.progress_var.set(f"{row_index} / {total}")
            self.status_var.set(f"OCR中: {image_path.name}")
            self.root.update_idletasks()

        for column_cells in sheet.columns:
            max_length = max(len(str(cell.value or "")) for cell in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 12), 42)

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(self.output_path)
        self.progress_var.set("")
        self.status_var.set(f"Excelへ出力しました: {self.output_path}")
        messagebox.showinfo("完了", f"{total} 画像をExcelへ出力しました。\n{self.output_path}")

    def _load_tesseract(self, show_errors: bool):
        if pytesseract is None:
            if show_errors:
                messagebox.showerror("OCRライブラリ未導入", "pytesseractがインストールされていません。uv sync を実行してください。")
            return None
        path = self.tesseract_var.get().strip()
        if path:
            pytesseract.pytesseract.tesseract_cmd = path
        return pytesseract

    def _prepare_for_ocr(self, image: Image.Image) -> Image.Image:
        scale = 3 if max(image.size) < 500 else 2
        image = image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
        image = ImageOps.grayscale(image)
        image = ImageEnhance.Contrast(image).enhance(1.8)
        image = image.filter(ImageFilter.SHARPEN)
        return image

    def _clean_text(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines()]
        return " ".join(line for line in lines if line).strip()

    def _detect_tesseract(self) -> str:
        found = shutil.which("tesseract")
        if found:
            return found
        default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        return str(default) if default.exists() else ""

    def _initial_zoom(self) -> float:
        if not self.original_image:
            return 1.0
        max_w = max(self.canvas.winfo_width(), 900)
        max_h = max(self.canvas.winfo_height(), 600)
        w, h = self.original_image.size
        return min(1.0, max(0.15, min(max_w / w, max_h / h)))

    def _enabled_fields(self) -> list[TemplateField]:
        return [field for field in self.fields if field.enabled]

    def _ensure_current_results(self) -> None:
        if len(self.current_results) < len(self.fields):
            self.current_results.extend([""] * (len(self.fields) - len(self.current_results)))
        elif len(self.current_results) > len(self.fields):
            self.current_results = self.current_results[: len(self.fields)]

    def _set_result(self, idx: int, value: str) -> None:
        self._ensure_current_results()
        if 0 <= idx < len(self.current_results):
            self.current_results[idx] = value

    def _sync_counts(self) -> None:
        enabled = len(self._enabled_fields())
        total = len(self.fields)
        self.field_count_var.set(f"{enabled} / {total} 項目")

    def _field_order_label(self, idx: int) -> str:
        return f"#{idx + 1}"

    def _field_size_text(self, field: TemplateField) -> str:
        normalized = field.normalized()
        return f"{normalized.x2 - normalized.x1} x {normalized.y2 - normalized.y1}px"

    def _unique_field_name(self, name: str, skip_index: int | None = None) -> str:
        used = {field.name for idx, field in enumerate(self.fields) if idx != skip_index}
        if name not in used:
            return name
        base = name
        count = 2
        while f"{base} {count}" in used:
            count += 1
        return f"{base} {count}"

    def _lang_display(self, value: str) -> str:
        labels = {
            "jpn+eng": "日本語 + English",
            "jpn": "日本語のみ",
            "eng": "English のみ",
        }
        return labels.get(value, value or self._lang_display(DEFAULT_LANG))

    def _lang_value(self, display: str) -> str:
        values = {
            "日本語 + English": "jpn+eng",
            "日本語のみ": "jpn",
            "English のみ": "eng",
        }
        return values.get(display, display or DEFAULT_LANG)

    def _sync_lang_from_display(self, display: str | None = None) -> None:
        self.lang_var.set(self._lang_value(display or self.lang_display_var.get()))


def main() -> None:
    root = ctk.CTk()
    ImageOcrExcelApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
