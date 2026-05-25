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
        self.tesseract_var = StringVar(value=self._detect_tesseract())
        self.status_var = StringVar(value="画像を開き、範囲をドラッグしてください。")

        self._build_ui()
        self._bind_shortcuts()

    def _build_ui(self) -> None:
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        top = ctk.CTkFrame(self.root, corner_radius=0)
        top.pack(side=TOP, fill="x")
        top.grid_columnconfigure(0, weight=1)
        top.grid_columnconfigure(1, weight=2)
        top.grid_columnconfigure(2, weight=1)

        image_panel = ctk.CTkFrame(top, corner_radius=8)
        image_panel.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)
        ctk.CTkLabel(image_panel, text="画像", font=UI_FONT_BOLD).pack(anchor="w", padx=12, pady=(10, 6))
        image_row = ctk.CTkFrame(image_panel, fg_color="transparent")
        image_row.pack(fill="x", padx=12)
        ctk.CTkButton(image_row, text="画像", command=self.open_image, font=UI_FONT).pack(side=LEFT, fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(image_row, text="フォルダ", command=self.open_image_folder, font=UI_FONT, fg_color="#64748b", hover_color="#475569").pack(side=LEFT, fill="x", expand=True, padx=(4, 0))
        nav_row = ctk.CTkFrame(image_panel, fg_color="transparent")
        nav_row.pack(fill="x", padx=12, pady=(8, 0))
        ctk.CTkButton(nav_row, text="◁", command=self.previous_image, width=54, font=UI_FONT).pack(side=LEFT, padx=(0, 6))
        self.image_count_var = StringVar(value="0 / 0")
        ctk.CTkLabel(nav_row, textvariable=self.image_count_var, anchor="center", font=UI_FONT).pack(side=LEFT, fill="x", expand=True)
        ctk.CTkButton(nav_row, text="▷", command=self.next_image, width=54, font=UI_FONT).pack(side=LEFT, padx=(6, 0))
        mapping_row = ctk.CTkFrame(image_panel, fg_color="transparent")
        mapping_row.pack(fill="x", padx=12, pady=(8, 10))
        ctk.CTkButton(mapping_row, text="設定読込", command=self.load_mapping, width=92, font=UI_FONT).pack(side=LEFT, fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(mapping_row, text="設定保存", command=self.save_mapping, width=92, font=UI_FONT, fg_color="#64748b", hover_color="#475569").pack(side=LEFT, fill="x", expand=True, padx=(4, 0))

        excel_panel = ctk.CTkFrame(top, corner_radius=8)
        excel_panel.grid(row=0, column=1, sticky="nsew", padx=5, pady=10)
        excel_panel.grid_columnconfigure(0, weight=1)
        excel_panel.grid_columnconfigure(1, weight=0)
        excel_panel.grid_columnconfigure(2, weight=0)
        ctk.CTkLabel(excel_panel, text="Excel", font=UI_FONT_BOLD).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 6))
        ctk.CTkSegmentedButton(
            excel_panel,
            values=["開いているExcel", "ファイル出力"],
            variable=self.excel_mode_var,
            command=self.on_excel_mode_change,
            font=UI_FONT,
        ).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        ctk.CTkButton(excel_panel, text="更新", command=self.refresh_open_excel, width=72, font=UI_FONT).grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(0, 8))
        ctk.CTkButton(excel_panel, text="ファイル選択", command=self.select_excel, width=96, font=UI_FONT, fg_color="#64748b", hover_color="#475569").grid(row=1, column=2, sticky="ew", padx=(0, 12), pady=(0, 8))
        self.book_combo = ctk.CTkComboBox(excel_panel, variable=self.open_book_var, values=[], command=self.on_open_book_select, font=UI_FONT, dropdown_font=UI_FONT)
        self.book_combo.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 8))
        self.sheet_combo = ctk.CTkComboBox(excel_panel, variable=self.sheet_var, values=["Sheet1"], font=UI_FONT, dropdown_font=UI_FONT)
        self.sheet_combo.grid(row=2, column=2, sticky="ew", padx=(0, 12), pady=(0, 8))

        ocr_panel = ctk.CTkFrame(top, corner_radius=8)
        ocr_panel.grid(row=0, column=2, sticky="nsew", padx=(5, 10), pady=10)
        ctk.CTkLabel(ocr_panel, text="反映", font=UI_FONT_BOLD).pack(anchor="w", padx=12, pady=(10, 6))
        ctk.CTkLabel(ocr_panel, text="チェック済み範囲をOCRしてExcelへ書き込みます。", anchor="w", font=UI_FONT_SMALL).pack(fill="x", padx=12)
        ctk.CTkButton(ocr_panel, text="OCRしてExcelへ反映", command=self.write_excel, height=40, font=UI_FONT, fg_color="#16a34a", hover_color="#15803d").pack(fill="x", padx=12, pady=(10, 10))

        settings = ctk.CTkFrame(self.root, corner_radius=0, fg_color="transparent")
        settings.pack(side=TOP, fill="x", padx=10, pady=(0, 6))
        ctk.CTkLabel(settings, text="OCR言語", font=UI_FONT).pack(side=LEFT)
        ctk.CTkEntry(settings, textvariable=self.lang_var, width=92, font=UI_FONT).pack(side=LEFT, padx=(6, 14))
        ctk.CTkLabel(settings, text="Tesseract", font=UI_FONT).pack(side=LEFT)
        ctk.CTkEntry(settings, textvariable=self.tesseract_var, width=360, font=UI_FONT).pack(side=LEFT, padx=(6, 14))
        ctk.CTkLabel(settings, textvariable=self.status_var, anchor="w", font=UI_FONT_SMALL).pack(side=LEFT, fill="x", expand=True)

        pathbar = ctk.CTkFrame(self.root, corner_radius=0, fg_color="transparent")
        pathbar.pack(side=TOP, fill="x", padx=10, pady=(0, 6))
        ctk.CTkLabel(pathbar, textvariable=self.image_var, anchor="w", font=UI_FONT_SMALL).pack(side=LEFT, fill="x", expand=True, padx=(0, 8))
        ctk.CTkLabel(pathbar, textvariable=self.excel_var, anchor="e", font=UI_FONT_SMALL).pack(side=RIGHT, fill="x", expand=True)

        main = ctk.CTkFrame(self.root, corner_radius=0, fg_color="transparent")
        main.pack(side=TOP, fill=BOTH, expand=True)

        canvas_frame = ctk.CTkFrame(main, corner_radius=8)
        canvas_frame.pack(side=LEFT, fill=BOTH, expand=True, padx=(10, 6), pady=(0, 10))

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

        side = ctk.CTkFrame(main, width=360, corner_radius=8)
        side.pack(side=RIGHT, fill="y", padx=(6, 10), pady=(0, 10))
        side.pack_propagate(False)

        region_box = ctk.CTkFrame(side, corner_radius=8)
        region_box.pack(side=TOP, fill=BOTH, expand=True)
        ctk.CTkLabel(region_box, text="取得範囲とセル", font=UI_FONT_BOLD).pack(anchor="w", padx=12, pady=(12, 8))

        self.region_list_frame = ctk.CTkScrollableFrame(region_box, corner_radius=6)
        self.region_list_frame.pack(side=TOP, fill=BOTH, expand=True, padx=12)

        btns = ctk.CTkFrame(region_box, fg_color="transparent")
        btns.pack(side=TOP, fill="x", padx=12, pady=8)
        ctk.CTkButton(btns, text="セル", command=self.edit_cell, width=70, font=UI_FONT).pack(side=LEFT, fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(btns, text="範囲", command=self.reselect_region, width=70, font=UI_FONT, fg_color="#64748b", hover_color="#475569").pack(side=LEFT, fill="x", expand=True, padx=4)
        ctk.CTkButton(btns, text="削除", command=self.delete_region, width=70, font=UI_FONT, fg_color="#dc2626", hover_color="#b91c1c").pack(side=LEFT, fill="x", expand=True, padx=(4, 0))

        info = ctk.CTkFrame(side, corner_radius=8)
        info.pack(side=BOTTOM, fill="x", pady=(8, 0))
        ctk.CTkLabel(info, text="ドラッグ: 範囲追加 / Ctrl+ホイール: 拡大縮小\nCtrl+←/→: 画像切替 / Ctrl+Enter: Excel反映", anchor="w", justify="left", font=UI_FONT_SMALL).pack(fill="x", padx=12, pady=12)

    def _detect_tesseract(self) -> str:
        found = shutil.which("tesseract")
        if found:
            return found
        default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        return str(default) if default.exists() else ""

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
        sheets = list(book["sheets"])
        self.sheet_combo.configure(values=sheets)
        if self.sheet_var.get() not in sheets:
            self.sheet_var.set(str(book.get("active_sheet") or sheets[0]))
        self.excel_target_mode = "open"
        self.excel_mode_var.set("開いているExcel")
        self.excel_var.set(f"開いているExcel: {book['display']}")

    def on_excel_mode_change(self, value: str) -> None:
        if value == "開いているExcel":
            self.use_open_excel()
        else:
            self.use_file_excel()

    def use_open_excel(self, show_message: bool = True) -> None:
        if not self.open_excel_books:
            self.refresh_open_excel()
        book = self._selected_open_book()
        if not book:
            return
        self.excel_target_mode = "open"
        self.excel_mode_var.set("開いているExcel")
        self.excel_var.set(f"開いているExcel: {book['display']}")
        if show_message:
            self.status_var.set("開いているExcelへ反映する設定にしました。")

    def use_file_excel(self) -> None:
        self.excel_target_mode = "file"
        self.excel_mode_var.set("ファイル出力")
        self.excel_var.set(str(self.excel_path) if self.excel_path else "Excel未選択")
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
        width = 3 if idx == self.selected_index else 2
        rect = self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=width)
        label = self.canvas.create_text(
            x1 + 4,
            y1 + 4,
            anchor="nw",
            text=f"{idx + 1}: {region.cell}",
            fill="white",
            font=UI_FONT_CANVAS,
        )
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
            suffix = f"  {text}" if text else "  未OCR"
            row_color = "#dbeafe" if idx == self.selected_index else "transparent"
            row = ctk.CTkFrame(self.region_list_frame, fg_color=row_color, corner_radius=6)
            row.pack(fill="x", pady=2)
            row.grid_columnconfigure(1, weight=1)
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
            title = ctk.CTkLabel(row, text=f"{idx + 1}. {region.name} -> {region.cell}", anchor="w", font=UI_FONT)
            title.grid(row=0, column=1, sticky="ew", padx=(2, 8), pady=(6, 0))
            value = ctk.CTkLabel(row, text=suffix.strip(), anchor="w", text_color="#475569", font=UI_FONT_SMALL)
            value.grid(row=1, column=1, sticky="ew", padx=(2, 8), pady=(0, 6))
            for widget in (row, title, value):
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

    def save_mapping(self) -> None:
        if not self.regions:
            messagebox.showinfo("範囲なし", "保存する範囲がありません。")
            return
        file_name = filedialog.asksaveasfilename(
            title="設定を保存",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not file_name:
            return
        data = {
            "image_path": str(self.image_path) if self.image_path else "",
            "excel_path": str(self.excel_path) if self.excel_path else "",
            "excel_target_mode": self.excel_target_mode,
            "open_book": self.open_book_var.get(),
            "sheet": self.sheet_var.get(),
            "lang": self.lang_var.get(),
            "regions": [asdict(region.normalized()) for region in self.regions],
        }
        Path(file_name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.status_var.set(f"設定を保存しました: {file_name}")

    def load_mapping(self) -> None:
        file_name = filedialog.askopenfilename(
            title="設定を読み込み",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not file_name:
            return
        data = json.loads(Path(file_name).read_text(encoding="utf-8"))
        if data.get("image_path") and Path(data["image_path"]).exists():
            self.image_files = [Path(data["image_path"])]
            self.current_image_index = 0
            self._load_current_image(auto_ocr=False)
        if data.get("excel_path"):
            self.excel_path = Path(data["excel_path"])
            self.excel_var.set(str(self.excel_path))
        self.excel_target_mode = data.get("excel_target_mode") or "file"
        self.excel_mode_var.set("開いているExcel" if self.excel_target_mode == "open" else "ファイル出力")
        if data.get("open_book"):
            self.open_book_var.set(data["open_book"])
        self.sheet_var.set(data.get("sheet") or "Sheet1")
        self.lang_var.set(data.get("lang") or DEFAULT_LANG)
        self.regions = [Region(**item).normalized() for item in data.get("regions", [])]
        self.selected_index = 0 if self.regions else None
        self.refresh_region_list()
        self.redraw()
        self.status_var.set(f"設定を読み込みました: {file_name}")


def main() -> None:
    root = ctk.CTk()
    ImageOcrExcelApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
