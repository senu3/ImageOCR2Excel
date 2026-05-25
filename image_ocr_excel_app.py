from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import (
    BOTH,
    BOTTOM,
    BooleanVar,
    HORIZONTAL,
    LEFT,
    RIGHT,
    TOP,
    VERTICAL,
    Canvas,
    Scrollbar,
    StringVar,
    filedialog,
    messagebox,
    simpledialog,
)

import customtkinter as ctk
from openpyxl import Workbook, load_workbook
from openpyxl.utils.cell import coordinate_to_tuple
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageTk

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    import win32com.client
except ImportError:
    win32com = None


APP_TITLE = "Image OCR to Excel"
DEFAULT_LANG = "jpn+eng"
CELL_RE = re.compile(r"^[A-Za-z]{1,3}[1-9][0-9]*$")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
UI_FONT_FAMILY = "Meiryo"
UI_FONT = (UI_FONT_FAMILY, 13)
UI_FONT_SMALL = (UI_FONT_FAMILY, 12)
UI_FONT_BOLD = (UI_FONT_FAMILY, 14, "bold")
UI_FONT_CANVAS = (UI_FONT_FAMILY, 11, "bold")


@dataclass
class Region:
    name: str
    cell: str
    x1: int
    y1: int
    x2: int
    y2: int
    text: str = ""
    enabled: bool = True

    def normalized(self) -> "Region":
        x1, x2 = sorted((self.x1, self.x2))
        y1, y2 = sorted((self.y1, self.y2))
        return Region(self.name, self.cell, x1, y1, x2, y2, self.text, self.enabled)


class ImageOcrExcelApp:
    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1180x780")
        self.root.minsize(980, 640)
        self.root.option_add("*Font", f"{UI_FONT_FAMILY} 10")

        self.image_path: Path | None = None
        self.image_files: list[Path] = []
        self.current_image_index: int = -1
        self.excel_path: Path | None = None
        self.open_excel_books: list[dict[str, object]] = []
        self.excel_target_mode = "open"
        self.original_image: Image.Image | None = None
        self.preview_image: ImageTk.PhotoImage | None = None
        self.regions: list[Region] = []
        self.canvas_rects: dict[int, int] = {}
        self.canvas_labels: dict[int, int] = {}
        self.region_check_vars: list[BooleanVar] = []
        self.selected_index: int | None = None

        self.zoom = 1.0
        self.image_item: int | None = None
        self.drag_start: tuple[int, int] | None = None
        self.drag_rect: int | None = None
        self.reselect_region_index: int | None = None

        self.image_var = StringVar(value="画像未選択")
        self.excel_var = StringVar(value="Excel未選択")
        self.excel_mode_var = StringVar(value="開いているExcel")
        self.open_book_var = StringVar(value="")
        self.sheet_var = StringVar(value="Sheet1")
        self.lang_var = StringVar(value=DEFAULT_LANG)
        self.lang_display_var = StringVar(value=self._lang_display(DEFAULT_LANG))
        self.tesseract_var = StringVar(value=self._detect_tesseract())
        self.config_file_var = StringVar(value="ocr-config.json")
        self.status_var = StringVar(value="画像を開き、範囲をドラッグしてください。")
        self.settings_window: ctk.CTkToplevel | None = None

        self._build_ui()
        self._bind_shortcuts()

    def _build_ui(self) -> None:
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self.root.configure(fg_color="#eef2f6")

        main = ctk.CTkFrame(self.root, corner_radius=0, fg_color="transparent")
        main.pack(side=TOP, fill=BOTH, expand=True)

        canvas_area = ctk.CTkFrame(main, corner_radius=0, fg_color="#1f2937")
        canvas_area.pack(side=LEFT, fill=BOTH, expand=True, padx=(10, 0), pady=10)

        canvas_toolbar = ctk.CTkFrame(canvas_area, height=42, corner_radius=0, fg_color="#111827")
        canvas_toolbar.pack(side=TOP, fill="x")
        canvas_toolbar.pack_propagate(False)
        ctk.CTkButton(canvas_toolbar, text="画像を開く", command=self.open_image, width=96, height=26, font=UI_FONT_SMALL).pack(side=LEFT, padx=(10, 4), pady=8)
        ctk.CTkButton(canvas_toolbar, text="フォルダ", command=self.open_image_folder, width=82, height=26, font=UI_FONT_SMALL, fg_color="#475569", hover_color="#334155").pack(side=LEFT, padx=4, pady=8)
        ctk.CTkFrame(canvas_toolbar, width=1, height=20, fg_color="#374151").pack(side=LEFT, padx=8, pady=11)
        ctk.CTkButton(canvas_toolbar, text="設定", command=self.open_settings_modal, width=70, height=26, font=UI_FONT_SMALL, fg_color="#475569", hover_color="#334155").pack(side=LEFT, padx=4, pady=8)
        ctk.CTkFrame(canvas_toolbar, width=1, height=20, fg_color="#374151").pack(side=LEFT, padx=8, pady=11)
        ctk.CTkLabel(canvas_toolbar, text="ドラッグで範囲を追加", font=UI_FONT_SMALL, text_color="#cbd5e1").pack(side=LEFT, padx=(4, 0))
        self.image_count_var = StringVar(value="0 / 0")
        ctk.CTkButton(canvas_toolbar, text="▷", command=self.next_image, width=34, height=26, font=UI_FONT_SMALL, fg_color="#374151", hover_color="#4b5563").pack(side=RIGHT, padx=(4, 10), pady=8)
        ctk.CTkLabel(canvas_toolbar, textvariable=self.image_count_var, width=64, anchor="center", font=UI_FONT_SMALL, text_color="#cbd5e1").pack(side=RIGHT)
        ctk.CTkButton(canvas_toolbar, text="◁", command=self.previous_image, width=34, height=26, font=UI_FONT_SMALL, fg_color="#374151", hover_color="#4b5563").pack(side=RIGHT, padx=4, pady=8)

        canvas_frame = ctk.CTkFrame(canvas_area, corner_radius=0, fg_color="#1f2937")
        canvas_frame.pack(side=TOP, fill=BOTH, expand=True)

        self.canvas = Canvas(canvas_frame, bg="#2b2f36", highlightthickness=0)
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
        self.canvas.bind("<Control-MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)

        status_bar = ctk.CTkFrame(canvas_area, height=28, corner_radius=0, fg_color="#111827")
        status_bar.pack(side=BOTTOM, fill="x")
        status_bar.pack_propagate(False)
        ctk.CTkLabel(status_bar, textvariable=self.image_var, anchor="w", font=UI_FONT_SMALL, text_color="#cbd5e1").pack(side=LEFT, fill="x", expand=True, padx=10)

        side = ctk.CTkFrame(main, width=340, corner_radius=0, fg_color="#ffffff")
        side.pack(side=RIGHT, fill="y", padx=(0, 10), pady=10)
        side.pack_propagate(False)

        excel_box = ctk.CTkFrame(side, corner_radius=0, fg_color="#ffffff")
        excel_box.pack(side=TOP, fill="x", padx=12, pady=(12, 8))
        excel_box.grid_columnconfigure(0, weight=1)
        excel_box.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(excel_box, text="出力先 Excel", font=UI_FONT_BOLD).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ctk.CTkSegmentedButton(
            excel_box,
            values=["開いているExcel", "ファイル出力"],
            variable=self.excel_mode_var,
            command=self.on_excel_mode_change,
            font=UI_FONT_SMALL,
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.book_combo = ctk.CTkComboBox(excel_box, variable=self.open_book_var, values=[], command=self.on_open_book_select, font=UI_FONT_SMALL, dropdown_font=UI_FONT_SMALL)
        self.book_combo.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.sheet_combo = ctk.CTkComboBox(excel_box, variable=self.sheet_var, values=["Sheet1"], font=UI_FONT_SMALL, dropdown_font=UI_FONT_SMALL)
        self.sheet_combo.grid(row=3, column=0, sticky="ew", padx=(0, 4), pady=(0, 8))
        self.excel_action_button = ctk.CTkButton(excel_box, text="更新", command=self.refresh_open_excel, height=28, font=UI_FONT_SMALL)
        self.excel_action_button.grid(row=3, column=1, sticky="ew", padx=(4, 0), pady=(0, 8))
        ctk.CTkLabel(excel_box, textvariable=self.excel_var, anchor="w", font=UI_FONT_SMALL, text_color="#64748b").grid(row=4, column=0, columnspan=2, sticky="ew")

        region_box = ctk.CTkFrame(side, corner_radius=0, fg_color="#ffffff")
        region_box.pack(side=TOP, fill=BOTH, expand=True)
        region_header = ctk.CTkFrame(region_box, corner_radius=0, fg_color="#ffffff")
        region_header.pack(side=TOP, fill="x", padx=12, pady=(8, 8))
        ctk.CTkLabel(region_header, text="取得範囲", font=UI_FONT_BOLD).pack(side=LEFT)

        self.region_list_frame = ctk.CTkScrollableFrame(region_box, corner_radius=6)
        self.region_list_frame.pack(side=TOP, fill=BOTH, expand=True, padx=12)

        btns = ctk.CTkFrame(region_box, fg_color="transparent")
        btns.pack(side=TOP, fill="x", padx=12, pady=8)
        ctk.CTkButton(btns, text="セル", command=self.edit_cell, width=70, font=UI_FONT).pack(side=LEFT, fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(btns, text="範囲", command=self.reselect_region, width=70, font=UI_FONT, fg_color="#64748b", hover_color="#475569").pack(side=LEFT, fill="x", expand=True, padx=4)
        ctk.CTkButton(btns, text="削除", command=self.delete_region, width=70, font=UI_FONT, fg_color="#dc2626", hover_color="#b91c1c").pack(side=LEFT, fill="x", expand=True, padx=(4, 0))

        footer = ctk.CTkFrame(side, corner_radius=0, fg_color="#ffffff")
        footer.pack(side=BOTTOM, fill="x", padx=12, pady=(8, 12))
        ctk.CTkButton(footer, text="OCRしてExcelへ反映", command=self.write_excel, height=40, font=UI_FONT, fg_color="#16a34a", hover_color="#15803d").pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(footer, textvariable=self.status_var, anchor="center", font=UI_FONT_SMALL, text_color="#64748b", wraplength=300).pack(fill="x")
        self._sync_excel_controls()

    def _detect_tesseract(self) -> str:
        found = shutil.which("tesseract")
        if found:
            return found
        default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        return str(default) if default.exists() else ""

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

    def open_settings_modal(self) -> None:
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.focus()
            return

        self.lang_display_var.set(self._lang_display(self.lang_var.get()))
        window = ctk.CTkToplevel(self.root)
        self.settings_window = window
        window.title("設定")
        window.geometry("360x360")
        window.resizable(False, False)
        window.transient(self.root)
        window.grab_set()
        window.configure(fg_color="#2f312f")

        panel = ctk.CTkFrame(window, corner_radius=0, fg_color="#2f312f")
        panel.pack(fill=BOTH, expand=True, padx=18, pady=18)
        panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(panel, text="設定", anchor="w", font=UI_FONT_BOLD, text_color="#f8fafc").grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(panel, text="OCR エンジンと言語の設定", anchor="w", font=UI_FONT_SMALL, text_color="#cbd5e1").grid(row=1, column=0, sticky="ew", pady=(6, 18))

        ctk.CTkLabel(panel, text="OCR 言語", anchor="w", font=UI_FONT_SMALL, text_color="#cbd5e1").grid(row=2, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkComboBox(
            panel,
            variable=self.lang_display_var,
            values=["日本語 + English", "日本語のみ", "English のみ"],
            command=self._sync_lang_from_display,
            height=32,
            font=UI_FONT,
            dropdown_font=UI_FONT,
            fg_color="#262826",
            border_color="#555955",
            button_color="#3f433f",
            button_hover_color="#555955",
            text_color="#f8fafc",
        ).grid(row=3, column=0, sticky="ew")

        ctk.CTkLabel(panel, text="Tesseract パス", anchor="w", font=UI_FONT_SMALL, text_color="#cbd5e1").grid(row=4, column=0, sticky="ew", pady=(14, 6))
        ctk.CTkEntry(panel, textvariable=self.tesseract_var, height=32, font=UI_FONT, fg_color="#262826", border_color="#555955", text_color="#f8fafc").grid(row=5, column=0, sticky="ew")

        ctk.CTkLabel(panel, text="設定ファイル", anchor="w", font=UI_FONT_SMALL, text_color="#cbd5e1").grid(row=6, column=0, sticky="ew", pady=(14, 6))
        file_row = ctk.CTkFrame(panel, fg_color="transparent")
        file_row.grid(row=7, column=0, sticky="ew")
        file_row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(file_row, textvariable=self.config_file_var, height=32, font=UI_FONT, fg_color="#262826", border_color="#555955", text_color="#f8fafc").grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(file_row, text="読込", command=self.load_mapping_from_settings, width=48, height=32, font=UI_FONT_SMALL, fg_color="#3f433f", hover_color="#555955").grid(row=0, column=1, padx=(0, 6))
        ctk.CTkButton(file_row, text="保存", command=self.save_mapping_from_settings, width=48, height=32, font=UI_FONT_SMALL, fg_color="#3f433f", hover_color="#555955").grid(row=0, column=2)

        action_row = ctk.CTkFrame(panel, fg_color="transparent")
        action_row.grid(row=8, column=0, sticky="ew", pady=(18, 0))
        action_row.grid_columnconfigure(0, weight=1)
        action_row.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(action_row, text="キャンセル", command=window.destroy, height=32, font=UI_FONT, fg_color="#2f312f", hover_color="#3f433f", border_width=1, border_color="#666a66").grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(action_row, text="保存", command=self.apply_settings_modal, height=32, font=UI_FONT, fg_color="#2f312f", hover_color="#3f433f", border_width=1, border_color="#666a66").grid(row=0, column=1, sticky="ew", padx=(4, 0))

    def apply_settings_modal(self) -> None:
        self._sync_lang_from_display()
        self.status_var.set("OCR設定を更新しました。")
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.destroy()

    def _settings_file_path(self) -> Path:
        raw_path = self.config_file_var.get().strip() or "ocr-config.json"
        path = Path(raw_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        return path

    def save_mapping_from_settings(self) -> None:
        self._sync_lang_from_display()
        self.save_mapping(self._settings_file_path())

    def load_mapping_from_settings(self) -> None:
        self.load_mapping(self._settings_file_path())
        self.lang_display_var.set(self._lang_display(self.lang_var.get()))

    def _bind_shortcuts(self) -> None:
        self.root.bind_all("<Control-Left>", self.previous_image)
        self.root.bind_all("<Control-Right>", self.next_image)
        self.root.bind_all("<Control-Return>", self.write_excel)

    def open_image(self) -> None:
        file_name = filedialog.askopenfilename(
            title="画像を選択",
            filetypes=[
                ("Image files", "*.png;*.jpg;*.jpeg;*.bmp;*.tif;*.tiff"),
                ("All files", "*.*"),
            ],
        )
        if not file_name:
            return
        self.image_files = [Path(file_name)]
        self.current_image_index = 0
        self._load_current_image(auto_ocr=False)

    def open_image_folder(self) -> None:
        folder_name = filedialog.askdirectory(title="画像フォルダを選択")
        if not folder_name:
            return
        folder = Path(folder_name)
        files = sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)
        if not files:
            messagebox.showinfo("画像なし", "選択したフォルダ内に対応画像がありません。")
            return
        self.image_files = files
        self.current_image_index = 0
        self._load_current_image(auto_ocr=bool(self.regions))

    def previous_image(self, _event=None) -> None:
        if len(self.image_files) <= 1:
            return
        self.current_image_index = (self.current_image_index - 1) % len(self.image_files)
        self._load_current_image(auto_ocr=True)

    def next_image(self, _event=None) -> None:
        if len(self.image_files) <= 1:
            return
        self.current_image_index = (self.current_image_index + 1) % len(self.image_files)
        self._load_current_image(auto_ocr=True)

    def _load_current_image(self, auto_ocr: bool) -> None:
        if not (0 <= self.current_image_index < len(self.image_files)):
            return
        self.image_path = self.image_files[self.current_image_index]
        self.original_image = Image.open(self.image_path).convert("RGB")
        self.zoom = self._initial_zoom()
        self.image_var.set(self._image_status_text())
        self.image_count_var.set(f"{self.current_image_index + 1} / {len(self.image_files)}")
        self.reselect_region_index = None
        self.redraw()
        if auto_ocr and self.regions:
            self._rerun_ocr_for_all_regions()
        else:
            self.status_var.set("画像を読み込みました。OCRしたい文字部分をドラッグしてください。")

    def _image_status_text(self) -> str:
        if not self.image_path:
            return "画像未選択"
        if len(self.image_files) > 1:
            return f"{self.current_image_index + 1}/{len(self.image_files)}: {self.image_path}"
        return str(self.image_path)

    def _rerun_ocr_for_all_regions(self) -> None:
        for region in self.regions:
            region.text = ""
        self.refresh_region_list()
        for idx in range(len(self.regions)):
            if not self._ocr_region(idx):
                self.refresh_region_list()
                return
        self.refresh_region_list()
        self.status_var.set(f"画像切替に合わせてOCRを再実行しました: {self.current_image_index + 1} / {len(self.image_files)}")

    def select_excel(self) -> None:
        file_name = filedialog.asksaveasfilename(
            title="Excelファイルを選択または作成",
            defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx")],
        )
        if not file_name:
            return
        self.excel_path = Path(file_name)
        self.excel_var.set(str(self.excel_path))
        self.excel_target_mode = "file"
        self.excel_mode_var.set("ファイル出力")
        self._sync_excel_controls()

    def refresh_open_excel(self, silent: bool = False) -> None:
        excel = self._get_excel_app(show_error=not silent)
        if excel is None:
            return

        books: list[dict[str, object]] = []
        try:
            for book in excel.Workbooks:
                sheets = [sheet.Name for sheet in book.Worksheets]
                full_name = str(book.FullName) if str(book.Path) else ""
                display = f"{book.Name} ({full_name})" if full_name else f"{book.Name} (未保存)"
                books.append(
                    {
                        "name": str(book.Name),
                        "full_name": full_name,
                        "display": display,
                        "sheets": sheets,
                        "active_sheet": str(book.ActiveSheet.Name),
                    }
                )
        except Exception as exc:
            messagebox.showerror("Excel取得エラー", f"開いているExcelブックを取得できませんでした。\n{exc}")
            return

        self.open_excel_books = books
        self.book_combo.configure(values=[str(book["display"]) for book in books])
        if not books:
            self.open_book_var.set("")
            self.sheet_combo.configure(values=[])
            self.status_var.set("開いているExcelブックがありません。")
            if not silent:
                messagebox.showinfo("Excel未検出", "開いているExcelブックがありません。")
            return

        current = self.open_book_var.get()
        displays = [str(book["display"]) for book in books]
        if current not in displays:
            self.open_book_var.set(displays[0])
        self.on_open_book_select()
        self.use_open_excel(show_message=False)
        self.status_var.set("開いているExcelブックを取得しました。")

    def on_open_book_select(self, _event=None) -> None:
        book = self._selected_open_book()
        if not book:
            self.sheet_combo.configure(values=[])
            return
        raw_sheets = book.get("sheets")
        sheets = [str(sheet) for sheet in raw_sheets] if isinstance(raw_sheets, list) else []
        self.sheet_combo.configure(values=sheets)
        if not sheets:
            self.sheet_var.set("")
            return
        if self.sheet_var.get() not in sheets:
            self.sheet_var.set(str(book.get("active_sheet") or sheets[0]))
        self.excel_target_mode = "open"
        self.excel_mode_var.set("開いているExcel")
        self.excel_var.set(f"開いているExcel: {book['display']}")
        self._sync_excel_controls()

    def on_excel_mode_change(self, value: str) -> None:
        if value == "開いているExcel":
            self.use_open_excel()
        else:
            self.use_file_excel()

    def _sync_excel_controls(self) -> None:
        if self.excel_target_mode == "file":
            self.excel_action_button.configure(
                text="ファイル選択",
                command=self.select_excel,
                fg_color="#64748b",
                hover_color="#475569",
            )
        else:
            self.excel_action_button.configure(
                text="更新",
                command=self.refresh_open_excel,
                fg_color=["#3B8ED0", "#1F6AA5"],
                hover_color=["#36719F", "#144870"],
            )

    def use_open_excel(self, show_message: bool = True) -> None:
        if not self.open_excel_books:
            self.refresh_open_excel()
        book = self._selected_open_book()
        if not book:
            return
        self.excel_target_mode = "open"
        self.excel_mode_var.set("開いているExcel")
        self.excel_var.set(f"開いているExcel: {book['display']}")
        self._sync_excel_controls()
        if show_message:
            self.status_var.set("開いているExcelへ反映する設定にしました。")

    def use_file_excel(self) -> None:
        self.excel_target_mode = "file"
        self.excel_mode_var.set("ファイル出力")
        self.excel_var.set(str(self.excel_path) if self.excel_path else "Excel未選択")
        self._sync_excel_controls()
        self.status_var.set("Excelファイルへ保存する設定にしました。")

    def _initial_zoom(self) -> float:
        if not self.original_image:
            return 1.0
        max_w = max(self.canvas.winfo_width(), 900)
        max_h = max(self.canvas.winfo_height(), 600)
        w, h = self.original_image.size
        return min(1.0, max(0.15, min(max_w / w, max_h / h)))

    def redraw(self) -> None:
        self.canvas.delete("all")
        self.canvas_rects.clear()
        self.canvas_labels.clear()
        if not self.original_image:
            return

        w, h = self.original_image.size
        scaled = self.original_image.resize((int(w * self.zoom), int(h * self.zoom)), Image.Resampling.LANCZOS)
        self.preview_image = ImageTk.PhotoImage(scaled)
        self.image_item = self.canvas.create_image(0, 0, anchor="nw", image=self.preview_image)
        self.canvas.configure(scrollregion=(0, 0, scaled.width, scaled.height))

        for idx, region in enumerate(self.regions):
            self._draw_region(idx)

    def _draw_region(self, idx: int) -> None:
        region = self.regions[idx].normalized()
        x1, y1, x2, y2 = [v * self.zoom for v in (region.x1, region.y1, region.x2, region.y2)]
        color = "#ff3366" if idx == self.selected_index else "#24c8ff"
        label_bg = "#ff3366" if idx == self.selected_index else "#1d4ed8"
        width = 3 if idx == self.selected_index else 2
        rect = self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=width)
        label_text = f"範囲{idx + 1} -> {region.cell}"
        label_x = x1 + 4
        label_y = max(4, y1 - 24)
        label = self.canvas.create_text(
            label_x + 7,
            label_y + 4,
            anchor="nw",
            text=label_text,
            fill="white",
            font=UI_FONT_CANVAS,
        )
        label_box = self.canvas.bbox(label)
        if label_box:
            bg = self.canvas.create_rectangle(
                label_box[0] - 5,
                label_box[1] - 3,
                label_box[2] + 5,
                label_box[3] + 3,
                fill=label_bg,
                outline=label_bg,
            )
            self.canvas.tag_lower(bg, label)
        self.canvas_rects[idx] = rect
        self.canvas_labels[idx] = label

    def on_mouse_down(self, event) -> None:
        if not self.original_image:
            return
        self.drag_start = (int(self.canvas.canvasx(event.x) / self.zoom), int(self.canvas.canvasy(event.y) / self.zoom))
        x = self.drag_start[0] * self.zoom
        y = self.drag_start[1] * self.zoom
        self.drag_rect = self.canvas.create_rectangle(x, y, x, y, outline="#ffe066", width=2, dash=(4, 2))

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

        if self.reselect_region_index is not None:
            idx = self.reselect_region_index
            self.reselect_region_index = None
            if not (0 <= idx < len(self.regions)):
                return
            region = self.regions[idx]
            region.x1 = x1
            region.y1 = y1
            region.x2 = x2
            region.y2 = y2
            region.text = ""
            self.selected_index = idx
            self.refresh_region_list()
            self.redraw()
            self._ocr_region(idx)
            self.refresh_region_list()
            return

        default_cell = f"A{len(self.regions) + 1}"
        cell = simpledialog.askstring("セル指定", "この範囲を書き込むセルを入力してください。", initialvalue=default_cell)
        if cell is None:
            return
        cell = cell.strip().upper()
        if not self._valid_cell(cell):
            messagebox.showerror("セル指定エラー", "A1形式のセル番地を入力してください。")
            return

        region = Region(f"範囲{len(self.regions) + 1}", cell, x1, y1, x2, y2)
        self.regions.append(region)
        self.selected_index = len(self.regions) - 1
        self.refresh_region_list()
        self.redraw()
        self._ocr_region(self.selected_index)
        self.refresh_region_list()

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

    def select_region(self, idx: int) -> None:
        self.selected_index = idx
        self.refresh_region_list()
        self.redraw()

    def refresh_region_list(self) -> None:
        for child in self.region_list_frame.winfo_children():
            child.destroy()
        self.region_check_vars = []
        for idx, region in enumerate(self.regions):
            text = region.text.replace("\n", " ").strip()
            result_text = text if text else "OCR 未実行"
            row_color = "#e8f1ff" if idx == self.selected_index else "#ffffff"
            border_color = "#2563eb" if idx == self.selected_index else "#d7dee8"
            row = ctk.CTkFrame(self.region_list_frame, fg_color=row_color, corner_radius=6, border_width=1, border_color=border_color)
            row.pack(fill="x", pady=3)
            row.grid_columnconfigure(2, weight=1)
            var = BooleanVar(value=region.enabled)
            self.region_check_vars.append(var)
            checkbox = ctk.CTkCheckBox(
                row,
                text="",
                variable=var,
                width=28,
                font=UI_FONT,
                command=lambda i=idx, v=var: self.set_region_enabled(i, v.get()),
            )
            checkbox.grid(row=0, column=0, rowspan=2, padx=(8, 2), pady=8)
            label = ctk.CTkLabel(
                row,
                text=f"範囲{idx + 1}",
                font=UI_FONT_SMALL,
                text_color="#ffffff",
                fg_color="#2563eb",
                corner_radius=4,
                width=54,
                height=22,
            )
            label.grid(row=0, column=1, sticky="w", padx=(4, 6), pady=(8, 2))
            cell = ctk.CTkLabel(row, text=f"-> {region.cell}", anchor="w", font=UI_FONT_SMALL, text_color="#64748b")
            cell.grid(row=0, column=2, sticky="ew", padx=(0, 8), pady=(8, 2))
            value = ctk.CTkLabel(row, text=result_text, anchor="w", text_color="#1f2937" if text else "#94a3b8", font=UI_FONT_SMALL)
            value.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(4, 8), pady=(0, 8))
            for widget in (row, label, cell, value):
                widget.bind("<Button-1>", lambda _event, i=idx: self.select_region(i))

    def set_region_enabled(self, idx: int, enabled: bool) -> None:
        if 0 <= idx < len(self.regions):
            self.regions[idx].enabled = enabled

    def edit_cell(self) -> None:
        idx = self._require_selection()
        if idx is None:
            return
        cell = simpledialog.askstring("セル変更", "書き込み先セルを入力してください。", initialvalue=self.regions[idx].cell)
        if cell is None:
            return
        cell = cell.strip().upper()
        if not self._valid_cell(cell):
            messagebox.showerror("セル指定エラー", "A1形式のセル番地を入力してください。")
            return
        self.regions[idx].cell = cell
        self.refresh_region_list()
        self.redraw()
        self._ocr_region(idx)
        self.refresh_region_list()

    def reselect_region(self) -> None:
        idx = self._require_selection()
        if idx is None:
            return
        self.reselect_region_index = idx
        self.status_var.set(f"{self.regions[idx].name} の範囲を再設定します。画像上で新しい範囲をドラッグしてください。")

    def delete_region(self) -> None:
        idx = self._require_selection()
        if idx is None:
            return
        del self.regions[idx]
        self.selected_index = min(idx, len(self.regions) - 1) if self.regions else None
        self.refresh_region_list()
        self.redraw()

    def _require_selection(self) -> int | None:
        if self.selected_index is None or not (0 <= self.selected_index < len(self.regions)):
            messagebox.showinfo("範囲未選択", "右側の一覧から範囲を選択してください。")
            return None
        return self.selected_index

    def _valid_cell(self, cell: str) -> bool:
        if not CELL_RE.match(cell):
            return False
        try:
            coordinate_to_tuple(cell)
            return True
        except ValueError:
            return False

    def ocr_selected(self) -> None:
        idx = self._require_selection()
        if idx is None:
            return
        self._ocr_region(idx)
        self.refresh_region_list()

    def ocr_all(self) -> None:
        if not self.regions:
            messagebox.showinfo("範囲なし", "先に画像上で取得範囲を作成してください。")
            return
        for idx in range(len(self.regions)):
            self._ocr_region(idx)
        self.refresh_region_list()

    def _ocr_region(self, idx: int) -> bool:
        if not self.original_image:
            messagebox.showerror("画像未選択", "先に画像を開いてください。")
            return False
        ocr = self._load_tesseract()
        if ocr is None:
            return False
        region = self.regions[idx].normalized()
        crop = self.original_image.crop((region.x1, region.y1, region.x2, region.y2))
        prepared = self._prepare_for_ocr(crop)
        try:
            text = ocr.image_to_string(
                prepared,
                lang=self.lang_var.get().strip() or DEFAULT_LANG,
                config="--oem 3 --psm 6",
            )
        except ocr.TesseractNotFoundError:
            messagebox.showerror("OCRエラー", "Tesseractが見つかりません。Tesseract欄に実行ファイルのパスを入力してください。")
            return False
        except ocr.TesseractError as exc:
            messagebox.showerror("OCRエラー", str(exc))
            return False
        cleaned = self._clean_text(text)
        self.regions[idx].text = cleaned
        self.status_var.set(f"{self.regions[idx].name} をOCRしました。")
        return True

    def _load_tesseract(self):
        if pytesseract is None:
            messagebox.showerror(
                "OCRライブラリ未導入",
                "pytesseractがインストールされていません。pip install -r requirements.txt を実行してください。",
            )
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
        lines = [line for line in lines if line]
        return " ".join(lines).strip()

    def _checked_region_indexes(self) -> list[int]:
        return [idx for idx, region in enumerate(self.regions) if region.enabled]

    def _checked_regions(self) -> list[Region]:
        return [self.regions[idx] for idx in self._checked_region_indexes()]

    def _ensure_checked_regions_ocr(self) -> bool:
        for idx in self._checked_region_indexes():
            if not self.regions[idx].text.strip():
                if not self._ocr_region(idx):
                    return False
        self.refresh_region_list()
        return True

    def write_excel(self, _event=None) -> None:
        if self.excel_target_mode == "open":
            self.write_open_excel()
            return
        if not self.excel_path:
            messagebox.showerror("Excel未選択", "先にExcelファイルを選択してください。")
            return
        if not self.regions:
            messagebox.showerror("範囲なし", "先に取得範囲を作成してください。")
            return
        target_regions = self._checked_regions()
        if not target_regions:
            messagebox.showerror("反映対象なし", "Excelへ反映する範囲にチェックを入れてください。")
            return
        if not self._ensure_checked_regions_ocr():
            return

        if self.excel_path.exists():
            workbook = load_workbook(self.excel_path)
        else:
            workbook = Workbook()

        sheet_name = self.sheet_var.get().strip() or "Sheet1"
        if sheet_name in workbook.sheetnames:
            sheet = workbook[sheet_name]
        else:
            sheet = workbook.create_sheet(sheet_name)
            if "Sheet" in workbook.sheetnames and len(workbook.sheetnames) > 1 and workbook["Sheet"].max_row == 1:
                default_sheet = workbook["Sheet"]
                if default_sheet["A1"].value is None:
                    workbook.remove(default_sheet)

        for region in target_regions:
            if not self._valid_cell(region.cell):
                messagebox.showerror("セル指定エラー", f"{region.name} のセル指定が不正です: {region.cell}")
                return
            sheet[region.cell] = region.text.strip()

        workbook.save(self.excel_path)
        self.status_var.set(f"Excelへ反映しました: {self.excel_path}")
        messagebox.showinfo("完了", "Excelへの反映が完了しました。")

    def write_open_excel(self) -> None:
        if not self.regions:
            messagebox.showerror("範囲なし", "先に取得範囲を作成してください。")
            return
        target_regions = self._checked_regions()
        if not target_regions:
            messagebox.showerror("反映対象なし", "Excelへ反映する範囲にチェックを入れてください。")
            return

        if not self.open_excel_books:
            self.refresh_open_excel()

        book_info = self._selected_open_book()
        if not book_info:
            messagebox.showerror("Excel未選択", "開いているブックを選択してください。")
            return

        if not self._ensure_checked_regions_ocr():
            return

        excel = self._get_excel_app(show_error=True)
        if excel is None:
            return

        try:
            workbook = self._find_open_workbook(excel, book_info)
            if workbook is None:
                messagebox.showerror("Excel未検出", "選択したブックが見つかりません。更新ボタンで再取得してください。")
                return

            sheet_name = self.sheet_var.get().strip() or str(book_info.get("active_sheet") or "")
            worksheet = workbook.Worksheets(sheet_name)
            written_cells = []
            for region in target_regions:
                if not self._valid_cell(region.cell):
                    messagebox.showerror("セル指定エラー", f"{region.name} のセル指定が不正です: {region.cell}")
                    return
                worksheet.Range(region.cell).Value = region.text.strip()
                written_cells.append(region.cell)
            worksheet.Activate()
            workbook.Activate()
        except Exception as exc:
            messagebox.showerror("Excel反映エラー", f"開いているExcelへ反映できませんでした。\n{exc}")
            return

        cells = ", ".join(written_cells)
        self.status_var.set(f"開いているExcelへ反映しました: {book_info['name']} / {self.sheet_var.get()} / {cells}")
        messagebox.showinfo("完了", f"開いているExcelへ{len(written_cells)}セル反映しました。\n反映先: {book_info['name']} / {self.sheet_var.get()}\nセル: {cells}\n保存はExcel側で行ってください。")

    def _get_excel_app(self, show_error: bool):
        if win32com is None:
            if show_error:
                messagebox.showerror("Excel連携ライブラリ未導入", "pywin32がインストールされていません。uv sync を実行してください。")
            return None
        try:
            return win32com.client.GetActiveObject("Excel.Application")
        except Exception:
            if show_error:
                messagebox.showerror("Excel未起動", "起動中のExcelを取得できませんでした。Excelで対象ブックを開いてから再実行してください。")
            return None

    def _selected_open_book(self) -> dict[str, object] | None:
        display = self.open_book_var.get()
        for book in self.open_excel_books:
            if book["display"] == display:
                return book
        return None

    def _find_open_workbook(self, excel, book_info: dict[str, object]):
        target_full_name = str(book_info.get("full_name") or "")
        target_name = str(book_info.get("name") or "")
        for workbook in excel.Workbooks:
            full_name = str(workbook.FullName) if str(workbook.Path) else ""
            if target_full_name and full_name == target_full_name:
                return workbook
            if not target_full_name and str(workbook.Name) == target_name:
                return workbook
        return None

    def save_mapping(self, file_name: str | Path | None = None) -> None:
        if file_name is None:
            file_name = filedialog.asksaveasfilename(
                title="設定を保存",
                defaultextension=".json",
                filetypes=[("JSON", "*.json")],
            )
            if not file_name:
                return
        path = Path(file_name)
        data = {
            "image_path": str(self.image_path) if self.image_path else "",
            "excel_path": str(self.excel_path) if self.excel_path else "",
            "excel_target_mode": self.excel_target_mode,
            "open_book": self.open_book_var.get(),
            "sheet": self.sheet_var.get(),
            "lang": self.lang_var.get(),
            "tesseract_path": self.tesseract_var.get(),
            "regions": [asdict(region.normalized()) for region in self.regions],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.config_file_var.set(str(path))
        self.status_var.set(f"設定を保存しました: {path}")

    def load_mapping(self, file_name: str | Path | None = None) -> None:
        if file_name is None:
            file_name = filedialog.askopenfilename(
                title="設定を読み込み",
                filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            )
            if not file_name:
                return
        path = Path(file_name)
        if not path.exists():
            messagebox.showerror("設定なし", f"設定ファイルが見つかりません。\n{path}")
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("image_path") and Path(data["image_path"]).exists():
            self.image_files = [Path(data["image_path"])]
            self.current_image_index = 0
            self._load_current_image(auto_ocr=False)
        if data.get("excel_path"):
            self.excel_path = Path(data["excel_path"])
            self.excel_var.set(str(self.excel_path))
        self.excel_target_mode = data.get("excel_target_mode") or "file"
        self.excel_mode_var.set("開いているExcel" if self.excel_target_mode == "open" else "ファイル出力")
        self._sync_excel_controls()
        if data.get("open_book"):
            self.open_book_var.set(data["open_book"])
        self.sheet_var.set(data.get("sheet") or "Sheet1")
        self.lang_var.set(data.get("lang") or DEFAULT_LANG)
        if data.get("tesseract_path"):
            self.tesseract_var.set(data["tesseract_path"])
        self.regions = [Region(**item).normalized() for item in data.get("regions", [])]
        self.selected_index = 0 if self.regions else None
        self.refresh_region_list()
        self.redraw()
        self.config_file_var.set(str(path))
        self.status_var.set(f"設定を読み込みました: {path}")


def main() -> None:
    root = ctk.CTk()
    ImageOcrExcelApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
