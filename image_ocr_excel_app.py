from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import (
    BOTH,
    BOTTOM,
    DISABLED,
    END,
    HORIZONTAL,
    LEFT,
    NORMAL,
    RIGHT,
    TOP,
    VERTICAL,
    Button,
    Canvas,
    Entry,
    Frame,
    Label,
    LabelFrame,
    Listbox,
    Menu,
    Scrollbar,
    StringVar,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
    simpledialog,
    ttk,
)

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


@dataclass
class Region:
    name: str
    cell: str
    x1: int
    y1: int
    x2: int
    y2: int
    text: str = ""

    def normalized(self) -> "Region":
        x1, x2 = sorted((self.x1, self.x2))
        y1, y2 = sorted((self.y1, self.y2))
        return Region(self.name, self.cell, x1, y1, x2, y2, self.text)


class ImageOcrExcelApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1180x780")
        self.root.minsize(980, 640)

        self.image_path: Path | None = None
        self.excel_path: Path | None = None
        self.open_excel_books: list[dict[str, object]] = []
        self.excel_target_mode = "file"
        self.original_image: Image.Image | None = None
        self.preview_image: ImageTk.PhotoImage | None = None
        self.regions: list[Region] = []
        self.canvas_rects: dict[int, int] = {}
        self.canvas_labels: dict[int, int] = {}
        self.selected_index: int | None = None

        self.zoom = 1.0
        self.image_item: int | None = None
        self.drag_start: tuple[int, int] | None = None
        self.drag_rect: int | None = None

        self.image_var = StringVar(value="画像未選択")
        self.excel_var = StringVar(value="Excel未選択")
        self.open_book_var = StringVar(value="")
        self.sheet_var = StringVar(value="Sheet1")
        self.lang_var = StringVar(value=DEFAULT_LANG)
        self.tesseract_var = StringVar(value=self._detect_tesseract())
        self.status_var = StringVar(value="画像を開き、範囲をドラッグしてください。")

        self._build_ui()

    def _build_ui(self) -> None:
        toolbar = Frame(self.root, padx=8, pady=8)
        toolbar.pack(side=TOP, fill="x")

        Button(toolbar, text="画像を開く", command=self.open_image).pack(side=LEFT, padx=(0, 6))
        Button(toolbar, text="Excelを選択", command=self.select_excel).pack(side=LEFT, padx=(0, 6))
        Button(toolbar, text="開いているExcel更新", command=self.refresh_open_excel).pack(side=LEFT, padx=(0, 6))
        Button(toolbar, text="設定保存", command=self.save_mapping).pack(side=LEFT, padx=(0, 6))
        Button(toolbar, text="設定読込", command=self.load_mapping).pack(side=LEFT, padx=(0, 12))

        Label(toolbar, text="OCR言語").pack(side=LEFT)
        Entry(toolbar, textvariable=self.lang_var, width=10).pack(side=LEFT, padx=(4, 12))
        Label(toolbar, text="Tesseract").pack(side=LEFT)
        Entry(toolbar, textvariable=self.tesseract_var, width=38).pack(side=LEFT, padx=(4, 8))

        Button(toolbar, text="選択範囲をOCR", command=self.ocr_selected).pack(side=LEFT, padx=(0, 6))
        Button(toolbar, text="全範囲をOCR", command=self.ocr_all).pack(side=LEFT, padx=(0, 6))
        Button(toolbar, text="Excelへ反映", command=self.write_excel).pack(side=LEFT)

        excelbar = Frame(self.root, padx=8)
        excelbar.pack(side=TOP, fill="x")
        Label(excelbar, text="開いているブック").pack(side=LEFT)
        self.book_combo = ttk.Combobox(excelbar, textvariable=self.open_book_var, width=42, state="readonly")
        self.book_combo.pack(side=LEFT, padx=(4, 12))
        self.book_combo.bind("<<ComboboxSelected>>", self.on_open_book_select)
        Label(excelbar, text="シート").pack(side=LEFT)
        self.sheet_combo = ttk.Combobox(excelbar, textvariable=self.sheet_var, width=24)
        self.sheet_combo.pack(side=LEFT, padx=(4, 12))
        Button(excelbar, text="開いているExcelを使用", command=self.use_open_excel).pack(side=LEFT, padx=(0, 6))
        Button(excelbar, text="ファイル出力を使用", command=self.use_file_excel).pack(side=LEFT)

        pathbar = Frame(self.root, padx=8)
        pathbar.pack(side=TOP, fill="x")
        Label(pathbar, textvariable=self.image_var, anchor="w").pack(side=LEFT, fill="x", expand=True)
        Label(pathbar, textvariable=self.excel_var, anchor="w").pack(side=RIGHT, fill="x", expand=True)

        main = Frame(self.root, padx=8, pady=8)
        main.pack(side=TOP, fill=BOTH, expand=True)

        canvas_frame = Frame(main)
        canvas_frame.pack(side=LEFT, fill=BOTH, expand=True)

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

        side = Frame(main, width=340)
        side.pack(side=RIGHT, fill="y", padx=(8, 0))
        side.pack_propagate(False)

        region_box = LabelFrame(side, text="取得範囲とセル", padx=8, pady=8)
        region_box.pack(side=TOP, fill=BOTH, expand=True)

        self.region_list = Listbox(region_box, height=12, exportselection=False)
        self.region_list.pack(side=TOP, fill=BOTH, expand=True)
        self.region_list.bind("<<ListboxSelect>>", self.on_region_select)

        btns = Frame(region_box, pady=6)
        btns.pack(side=TOP, fill="x")
        Button(btns, text="セル変更", command=self.edit_cell).pack(side=LEFT, padx=(0, 4))
        Button(btns, text="名前変更", command=self.edit_name).pack(side=LEFT, padx=(0, 4))
        Button(btns, text="削除", command=self.delete_region).pack(side=LEFT)

        Label(region_box, text="OCR結果 / Excelへ書き込む値", anchor="w").pack(side=TOP, fill="x")
        self.text_edit = ttk.Frame(region_box)
        self.text_edit.pack(side=TOP, fill=BOTH, expand=False)
        self.text_box = ttk.Entry(self.text_edit)
        self.text_box.pack(side=LEFT, fill="x", expand=True)
        Button(self.text_edit, text="反映", command=self.apply_text_edit).pack(side=LEFT, padx=(4, 0))

        info = LabelFrame(side, text="操作", padx=8, pady=8)
        info.pack(side=BOTTOM, fill="x", pady=(8, 0))
        Label(info, text="ドラッグ: 範囲追加 / Ctrl+ホイール: 拡大縮小", anchor="w").pack(fill="x")
        Label(info, textvariable=self.status_var, anchor="w", wraplength=310, justify=LEFT).pack(fill="x", pady=(6, 0))

    def _detect_tesseract(self) -> str:
        found = shutil.which("tesseract")
        if found:
            return found
        default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        return str(default) if default.exists() else ""

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
        self.image_path = Path(file_name)
        self.original_image = Image.open(self.image_path).convert("RGB")
        self.zoom = self._initial_zoom()
        self.image_var.set(str(self.image_path))
        self.redraw()
        self.status_var.set("画像を読み込みました。OCRしたい文字部分をドラッグしてください。")

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
        self.book_combo["values"] = [str(book["display"]) for book in books]
        if not books:
            self.open_book_var.set("")
            self.sheet_combo["values"] = []
            self.status_var.set("開いているExcelブックがありません。")
            if not silent:
                messagebox.showinfo("Excel未検出", "開いているExcelブックがありません。")
            return

        current = self.open_book_var.get()
        displays = [str(book["display"]) for book in books]
        if current not in displays:
            self.open_book_var.set(displays[0])
        self.on_open_book_select()
        self.status_var.set("開いているExcelブックを取得しました。")

    def on_open_book_select(self, _event=None) -> None:
        book = self._selected_open_book()
        if not book:
            self.sheet_combo["values"] = []
            return
        sheets = list(book["sheets"])
        self.sheet_combo["values"] = sheets
        if self.sheet_var.get() not in sheets:
            self.sheet_var.set(str(book.get("active_sheet") or sheets[0]))

    def use_open_excel(self) -> None:
        if not self.open_excel_books:
            self.refresh_open_excel()
        book = self._selected_open_book()
        if not book:
            return
        self.excel_target_mode = "open"
        self.excel_var.set(f"開いているExcel: {book['display']}")
        self.status_var.set("開いているExcelへ反映する設定にしました。")

    def use_file_excel(self) -> None:
        self.excel_target_mode = "file"
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
            font=("Yu Gothic UI", 11, "bold"),
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

    def on_region_select(self, _event=None) -> None:
        selection = self.region_list.curselection()
        if not selection:
            self.selected_index = None
            self.text_box.delete(0, END)
            self.redraw()
            return
        self.selected_index = selection[0]
        self.text_box.delete(0, END)
        self.text_box.insert(0, self.regions[self.selected_index].text)
        self.redraw()

    def refresh_region_list(self) -> None:
        self.region_list.delete(0, END)
        for idx, region in enumerate(self.regions):
            text = region.text.replace("\n", " ").strip()
            suffix = f" = {text}" if text else ""
            self.region_list.insert(END, f"{idx + 1}. {region.name} -> {region.cell}{suffix}")
        if self.selected_index is not None and 0 <= self.selected_index < len(self.regions):
            self.region_list.selection_set(self.selected_index)
            self.region_list.see(self.selected_index)

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

    def edit_name(self) -> None:
        idx = self._require_selection()
        if idx is None:
            return
        name = simpledialog.askstring("名前変更", "範囲名を入力してください。", initialvalue=self.regions[idx].name)
        if not name:
            return
        self.regions[idx].name = name.strip()
        self.refresh_region_list()

    def delete_region(self) -> None:
        idx = self._require_selection()
        if idx is None:
            return
        del self.regions[idx]
        self.selected_index = min(idx, len(self.regions) - 1) if self.regions else None
        self.refresh_region_list()
        self.redraw()

    def apply_text_edit(self) -> None:
        idx = self._require_selection()
        if idx is None:
            return
        self.regions[idx].text = self.text_box.get().strip()
        self.refresh_region_list()

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

    def _ocr_region(self, idx: int) -> None:
        if not self.original_image:
            messagebox.showerror("画像未選択", "先に画像を開いてください。")
            return
        ocr = self._load_tesseract()
        if ocr is None:
            return
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
            return
        except ocr.TesseractError as exc:
            messagebox.showerror("OCRエラー", str(exc))
            return
        cleaned = self._clean_text(text)
        self.regions[idx].text = cleaned
        if idx == self.selected_index:
            self.text_box.delete(0, END)
            self.text_box.insert(0, cleaned)
        self.status_var.set(f"{self.regions[idx].name} をOCRしました。")

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

    def write_excel(self) -> None:
        if self.excel_target_mode == "open":
            self.write_open_excel()
            return
        if not self.excel_path:
            messagebox.showerror("Excel未選択", "先にExcelファイルを選択してください。")
            return
        if not self.regions:
            messagebox.showerror("範囲なし", "先に取得範囲を作成してください。")
            return
        empty = [region.name for region in self.regions if not region.text.strip()]
        if empty:
            proceed = messagebox.askyesno("未OCRの範囲があります", "空の値がある範囲があります。このままExcelへ反映しますか？")
            if not proceed:
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

        for region in self.regions:
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

        book_info = self._selected_open_book()
        if not book_info:
            messagebox.showerror("Excel未選択", "開いているブックを選択してください。")
            return

        empty = [region.name for region in self.regions if not region.text.strip()]
        if empty:
            proceed = messagebox.askyesno("未OCRの範囲があります", "空の値がある範囲があります。このままExcelへ反映しますか？")
            if not proceed:
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
            for region in self.regions:
                if not self._valid_cell(region.cell):
                    messagebox.showerror("セル指定エラー", f"{region.name} のセル指定が不正です: {region.cell}")
                    return
                worksheet.Range(region.cell).Value = region.text.strip()
            worksheet.Activate()
            workbook.Activate()
        except Exception as exc:
            messagebox.showerror("Excel反映エラー", f"開いているExcelへ反映できませんでした。\n{exc}")
            return

        self.status_var.set(f"開いているExcelへ反映しました: {book_info['name']} / {self.sheet_var.get()}")
        messagebox.showinfo("完了", "開いているExcelへの反映が完了しました。保存はExcel側で行ってください。")

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
            self.image_path = Path(data["image_path"])
            self.original_image = Image.open(self.image_path).convert("RGB")
            self.zoom = self._initial_zoom()
            self.image_var.set(str(self.image_path))
        if data.get("excel_path"):
            self.excel_path = Path(data["excel_path"])
            self.excel_var.set(str(self.excel_path))
        self.excel_target_mode = data.get("excel_target_mode") or "file"
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
    root = Tk()
    ImageOcrExcelApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
