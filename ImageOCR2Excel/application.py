from __future__ import annotations

import logging
import os
import sys
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import cast
from tkinter import (
    BOTH,
    BOTTOM,
    HORIZONTAL,
    LEFT,
    RIGHT,
    TOP,
    VERTICAL,
    BooleanVar,
    Canvas,
    Menu,
    StringVar,
    TclError,
    filedialog,
)

import customtkinter as ctk
from PIL import Image, ImageTk

from ImageOCR2Excel import __version__
from ImageOCR2Excel.ui.dialogs import AppDialogs
from ImageOCR2Excel.diagnostics import (
    configure_logging,
    current_log_path,
    ensure_log_directory,
    install_exception_hooks,
    shutdown_logging,
)
from ImageOCR2Excel.export.queue import (
    STATUS_EXCLUDED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PROCESSING,
    STATUS_SUCCESS,
    ImageQueue,
    ImageQueueItem,
)
from ImageOCR2Excel.export.csv import CsvExporter
from ImageOCR2Excel.export.excel import (
    ExcelExporter,
    ExportResult,
    ExportSettings,
    SetResolver,
    validate_export_settings,
)
from ImageOCR2Excel.app_config import ApplicationConfig
from ImageOCR2Excel.config import (
    IMAGE_EXTENSIONS,
    natural_image_sort_key,
)
from ImageOCR2Excel.ui.icons import ICONS
from ImageOCR2Excel.ui.theme import THEME
from ImageOCR2Excel.ui.modal import ModalOverlay
from ImageOCR2Excel.ocr.engine import (
    OcrEngine,
    apply_correction_rules,
    apply_postprocess,
    detect_content_rect,
    detect_line_bands,
    fill_missing_field_source_size,
    scaled_field,
)
from ImageOCR2Excel.ocr.environment import (
    OCR_ENV_LOCATION_ERROR,
    OCR_ENV_READY,
    OCR_ENV_SETUP,
    OCR_ENV_UNAVAILABLE,
    OCR_ENV_VERIFY,
    OcrEnvironmentManager,
    OcrEnvironmentStatus,
)
from ImageOCR2Excel.ocr.setup import OcrSetupPhase, OcrSetupProcessRunner
from ImageOCR2Excel.launcher.core import (
    LauncherError,
    launch_application_handoff,
    launch_repair_handoff,
)
from ImageOCR2Excel.models import (
    COORDINATE_SPACE_CONTENT,
    COORDINATE_SPACE_IMAGE,
    CORRECTION_ALL_TARGET,
    EXPORT_LAYOUT_IMAGE_ROW,
    EXPORT_LAYOUT_OPTIONS,
    EXPORT_LAYOUT_SET,
    LINE_JOIN_FULLWIDTH_SPACE,
    LINE_JOIN_NEWLINE,
    LINE_JOIN_NONE,
    CoordinateSettings,
    CorrectionRule,
    SetDetectionResult,
    TemplateField,
    TextFormattingSettings,
    set_validation_error,
)
from ImageOCR2Excel.operations import OperationCancelled, raise_if_cancelled
from ImageOCR2Excel.profiles.base import OcrProfile
from ImageOCR2Excel.templates import (
    build_template_data,
    coordinate_settings_from_template,
    correction_rules_from_template,
    fields_from_template,
    load_template,
    profile_options_from_template,
    save_template,
    set_definition_from_template,
    text_formatting_from_template,
)
from ImageOCR2Excel.ui.tooltip import add_tooltip


logger = logging.getLogger(__name__)


class AutoHideScrollableFrame(ctk.CTkScrollableFrame):
    """Show the scrollbar only while the frame content exceeds its viewport."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._scrollbar_visibility_job: str | None = None
        self._scrollbar_is_visible = True
        self.bind("<Configure>", self._schedule_scrollbar_visibility_update, add="+")
        self._parent_canvas.bind(
            "<Configure>", self._schedule_scrollbar_visibility_update, add="+"
        )
        self._schedule_scrollbar_visibility_update()

    def _schedule_scrollbar_visibility_update(self, _event=None) -> None:
        if self._scrollbar_visibility_job is not None:
            self.after_cancel(self._scrollbar_visibility_job)
        self._scrollbar_visibility_job = self.after_idle(
            self._update_scrollbar_visibility
        )

    def _update_scrollbar_visibility(self) -> None:
        self._scrollbar_visibility_job = None
        bbox = self._parent_canvas.bbox("all")
        if self._orientation == "vertical":
            content_size = 0 if bbox is None else bbox[3] - bbox[1]
            viewport_size = self._parent_canvas.winfo_height()
        else:
            content_size = 0 if bbox is None else bbox[2] - bbox[0]
            viewport_size = self._parent_canvas.winfo_width()

        should_show = viewport_size > 1 and content_size > viewport_size + 1
        if should_show == self._scrollbar_is_visible:
            return
        if should_show:
            self._scrollbar.grid()
        else:
            self._scrollbar.grid_remove()
        self._scrollbar_is_visible = should_show

    def destroy(self) -> None:
        if self._scrollbar_visibility_job is not None:
            self.after_cancel(self._scrollbar_visibility_job)
            self._scrollbar_visibility_job = None
        super().destroy()


UI_FONT_FAMILY = THEME.fonts.family
UI_FONT = THEME.fonts.normal
UI_FONT_SMALL = THEME.fonts.small
UI_FONT_BOLD = THEME.fonts.bold
UI_FONT_TITLE = THEME.fonts.title
UI_FONT_CANVAS = THEME.fonts.canvas

COLOR_BG = THEME.palette.app_bg
COLOR_SURFACE = THEME.palette.surface
COLOR_SURFACE_ALT = THEME.palette.surface_alt
COLOR_INPUT = THEME.palette.input_bg
COLOR_BORDER = THEME.palette.border
COLOR_TEXT = THEME.palette.text
COLOR_MUTED = THEME.palette.muted
COLOR_PRIMARY = THEME.palette.primary
COLOR_PRIMARY_HOVER = THEME.palette.primary_hover
COLOR_SECONDARY = THEME.palette.secondary
COLOR_SECONDARY_HOVER = THEME.palette.secondary_hover
COLOR_UTILITY_HOVER = THEME.palette.utility_hover
COLOR_DANGER = THEME.palette.danger
COLOR_DANGER_HOVER = THEME.palette.danger_hover
COLOR_CTA = THEME.palette.cta
COLOR_CTA_HOVER = THEME.palette.cta_hover
COLOR_CANVAS_BG = THEME.palette.canvas_bg
COLOR_CANVAS_PANEL = THEME.palette.canvas_panel
COLOR_CANVAS_TOOLBAR = THEME.palette.canvas_toolbar

OCR_MODE_OPTIONS = ["範囲全体", "行切り出し"]
LINE_JOIN_DISPLAY = {
    LINE_JOIN_NONE: "スペースなし",
    LINE_JOIN_FULLWIDTH_SPACE: "全角スペース",
    LINE_JOIN_NEWLINE: "改行",
}
EXPORT_LAYOUT_DISPLAY = {
    EXPORT_LAYOUT_IMAGE_ROW: "画像単位（1画像につき1行）",
    EXPORT_LAYOUT_SET: "セット単位（1セットにつき1行）",
}
EXPORT_LAYOUT_BY_DISPLAY = {
    display: value for value, display in EXPORT_LAYOUT_DISPLAY.items()
}
MODE_TEMPLATE = "1. 範囲・列"
MODE_REVIEW = "2. 認識テスト"
MODE_EXPORT = "3. Excel出力"


class ImageOcrExcelApp:
    def __init__(
        self,
        root: ctk.CTk,
        profile: OcrProfile,
        app_config: ApplicationConfig,
    ) -> None:
        self.root = root
        self.profile = profile
        self.app_config = app_config
        self.root.title(self.app_config.app_title)
        self.root.geometry(THEME.layout.window_size)
        self.root.minsize(*THEME.layout.min_window_size)
        self.root.option_add("*Font", f"{{{UI_FONT_FAMILY}}} 10")
        self.dialogs = AppDialogs(root)
        self.root.report_callback_exception = self._report_callback_exception
        self.dirty = False
        self._dirty_tracking_suspended = True

        self.image_path: Path | None = None
        self.image_files: list[Path] = []
        self.image_queue = ImageQueue()
        self.current_image_index = -1
        self.source_folder: Path | None = None
        self.output_path: Path | None = None
        self.template_path: Path | None = None
        self.workflow_started = False
        self.auto_detection_enabled = False
        self.auto_detection_base_fields: list[TemplateField] = []
        self.current_detection_layout = ""
        self.current_detection_reason = ""
        self.current_detection_count = 0

        self.original_image: Image.Image | None = None
        self.preview_image: ImageTk.PhotoImage | None = None
        self.preview_cache_key: tuple[int, float] | None = None
        self.image_item: int | None = None
        self.zoom = 1.0
        self.coordinate_settings = self.profile.coordinate_settings()
        self.text_formatting = self.profile.text_formatting_settings()
        self.image_text_correction_enabled = (
            self.profile.image_text_corrector is not None
        )
        self.current_content_rect: tuple[int, int, int, int] | None = None

        self.fields: list[TemplateField] = []
        self.set_definition = self.profile.default_set_definition
        self.current_results: list[str] = []
        self.current_raw_results: list[str] = []
        # Future correction-rule foundation.
        # Keep this data path even while the editor UI is hidden; template loading/saving
        # and OCR application already support it and will be reused when rules are redesigned.
        self.correction_rules: list[CorrectionRule] = []
        self.correction_rules_expanded = False
        self.result_vars: list[StringVar] = []
        self.field_check_vars: list[BooleanVar] = []
        self.field_row_widgets: list[ctk.CTkFrame] = []
        self.field_row_by_index: dict[int, ctk.CTkFrame] = {}
        self.field_drag_handle_by_index: dict[int, ctk.CTkLabel] = {}
        self.set_drop_section_by_id: dict[int, ctk.CTkFrame] = {}
        self.set_drag_target_id: int | None = None
        self.review_order_label_by_index: dict[int, ctk.CTkLabel] = {}
        self.selected_index: int | None = None
        self.editing_name_index: int | None = None
        self.editing_name_value: str = ""
        self.editing_name_error: str = ""
        self.editing_name_entry: ctk.CTkEntry | None = None
        self.editing_name_error_label: ctk.CTkLabel | None = None
        self.dragging_field_index: int | None = None
        self.canvas_edit_state: dict | None = None
        self.undo_action: dict | None = None

        self.drag_start: tuple[int, int] | None = None
        self.drag_rect: int | None = None
        self.canvas_rects: dict[int, int] = {}
        self.canvas_labels: dict[int, int] = {}
        self.canvas_label_bgs: dict[int, int] = {}
        self.canvas_set_handles: dict[int, dict] = {}
        self.empty_set_ids: set[int] = set()
        self.pending_set_id: int | None = None
        self.pending_slot_key: str = ""
        self.ocr_environment = OcrEnvironmentManager(
            app_directory_name=self.app_config.data_directory_name
        )
        self.ocr_environment_active_cache_dir = (
            self.ocr_environment.apply_to_process().resolve()
        )
        self.ocr_environment_restart_required = False
        self.ocr_environment_last_error = ""
        self.ocr_engine = OcrEngine(
            self.profile.image_text_corrector
            if self.image_text_correction_enabled
            else None,
            self.profile.image_text_fallback,
        )
        self.excel_exporter = ExcelExporter()
        self.csv_exporter = CsvExporter(self.excel_exporter)

        self.mode_var = StringVar(value=MODE_TEMPLATE)
        self.image_var = StringVar(value="画像未選択")
        self.folder_var = StringVar(value="未選択")
        self.output_var = StringVar(value="未選択")
        self.template_var = StringVar(value=THEME.copy.new_template_label)
        self.image_count_var = StringVar(value="0 / 0")
        self.status_var = StringVar(value=THEME.copy.initial_status)
        self.coordinate_status_var = StringVar(value="")
        self.progress_var = StringVar(value="")
        self.loading_var = StringVar(value="")
        self.loading_note_var = StringVar(value="")
        self.lang_var = StringVar(value=self.profile.default_lang)
        self.lang_display_var = StringVar(
            value=self._lang_display(self.profile.default_lang)
        )
        self.export_sheet_var = StringVar(value="OCR")
        self.export_write_mode_var = StringVar(value="上書き")
        self.export_start_cell_var = StringVar(value="A1")
        self.export_include_filename_var = BooleanVar(value=True)
        self.export_include_header_var = BooleanVar(value=True)
        self.export_layout_var = StringVar(value=EXPORT_LAYOUT_IMAGE_ROW)
        self.set_preset_var = StringVar(
            value=self.profile.set_preset_display[self.set_definition.preset]
        )
        self.review_raw_preview_var = StringVar(value="未OCR")
        self.review_processed_preview_var = StringVar(value="未OCR")
        self.review_line_split_var = StringVar(value=OCR_MODE_OPTIONS[0])
        self.review_crop_preview: ctk.CTkImage | None = None
        self.queue_include_vars: dict[Path, BooleanVar] = {}
        self.queue_status_labels: dict[Path, ctk.CTkLabel] = {}
        self.queue_summary_var = StringVar(value="処理対象 0 / 0")
        self.retry_button: ctk.CTkButton | None = None
        self.export_more_button: ctk.CTkButton | None = None
        self.last_export_result: ExportResult | None = None
        self.last_export_signature: tuple | None = None
        self.last_export_output_path: Path | None = None
        self.last_export_format = ""

        self.side_body: ctk.CTkFrame | None = None
        self.template_toolbar: ctk.CTkFrame | None = None
        self.template_save_button: ctk.CTkButton | None = None
        self.template_more_button: ctk.CTkButton | None = None
        self.review_detail_host: ctk.CTkFrame | None = None
        self.review_result_scroller: AutoHideScrollableFrame | None = None
        self.correction_rules_host: ctk.CTkFrame | None = None
        self.template_fields_scroller: AutoHideScrollableFrame | None = None
        self.canvas: Canvas
        self.canvas_frame: ctk.CTkFrame | None = None
        self.status_label: ctk.CTkLabel | None = None
        self.coordinate_status_label: ctk.CTkLabel | None = None
        self.workflow_choice_modal: ModalOverlay | None = None
        self.settings_modal: ModalOverlay | None = None
        self.ocr_environment_modal: ModalOverlay | None = None
        self.ocr_setup_modal: ModalOverlay | None = None
        self.workflow_choice_overlay: ctk.CTkFrame | None = None
        self.workflow_choice_primary_button: ctk.CTkButton | None = None
        self.ocr_setup_title_label: ctk.CTkLabel | None = None
        self.ocr_setup_subtitle_label: ctk.CTkLabel | None = None
        self.ocr_setup_status_label: ctk.CTkLabel | None = None
        self.ocr_setup_note_label: ctk.CTkLabel | None = None
        self.ocr_setup_progress: ctk.CTkProgressBar | None = None
        self.ocr_setup_primary_button: ctk.CTkButton | None = None
        self.ocr_setup_secondary_button: ctk.CTkButton | None = None
        self.ocr_setup_settings_button: ctk.CTkButton | None = None
        self.ocr_readiness_banner: ctk.CTkFrame | None = None
        self.ocr_readiness_label: ctk.CTkLabel | None = None
        self.ocr_readiness_button: ctk.CTkButton | None = None
        self._ocr_environment_ui_refresher = None
        self._pending_ocr_action = None
        self.ocr_setup_runner = OcrSetupProcessRunner()
        self._ocr_setup_operation = False
        self.hbar: ctk.CTkScrollbar | None = None
        self.vbar: ctk.CTkScrollbar | None = None
        self.loading_modal: ModalOverlay | None = None
        self.loading_overlay: ctk.CTkFrame | None = None
        self.loading_progress: ctk.CTkProgressBar | None = None
        self.loading_close_button: ctk.CTkButton | None = None
        self._side_render_job: str | None = None
        self.busy = False
        self._operation_cancel_event: threading.Event | None = None
        self._close_after_cancel = False

        self._build_ui()
        self._status_color_trace = self.status_var.trace_add(
            "write", self._reset_status_text_color
        )
        self._bind_shortcuts()
        self._bind_dirty_tracking()
        self._dirty_tracking_suspended = False
        self._update_dirty_indicators()
        self.root.protocol("WM_DELETE_WINDOW", self.request_close)

    def _build_ui(self) -> None:
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.root.configure(fg_color=COLOR_BG)

        main = ctk.CTkFrame(self.root, corner_radius=0, fg_color="transparent")
        main.pack(side=TOP, fill=BOTH, expand=True)

        canvas_area = ctk.CTkFrame(
            main,
            corner_radius=THEME.layout.panel_radius,
            fg_color=COLOR_CANVAS_PANEL,
            border_width=1,
            border_color=THEME.palette.canvas_border,
        )
        canvas_area.pack(side=LEFT, fill=BOTH, expand=True, padx=(12, 0), pady=12)

        toolbar = ctk.CTkFrame(
            canvas_area,
            height=THEME.layout.toolbar_height,
            corner_radius=0,
            fg_color=COLOR_CANVAS_TOOLBAR,
        )
        toolbar.pack(side=TOP, fill="x")
        toolbar.pack_propagate(False)
        self._toolbar_button(
            toolbar,
            "画像フォルダを選択",
            "folder",
            self.open_image_folder,
            COLOR_PRIMARY,
            COLOR_PRIMARY_HOVER,
            148,
        ).pack(side=LEFT, padx=(12, 4), pady=10)
        ctk.CTkFrame(
            toolbar, width=1, height=22, fg_color=THEME.palette.toolbar_divider
        ).pack(side=LEFT, padx=8, pady=16)
        ctk.CTkLabel(
            toolbar,
            textvariable=self.image_var,
            font=UI_FONT_SMALL,
            text_color=THEME.palette.toolbar_text,
            anchor="w",
        ).pack(side=LEFT, fill="x", expand=True, padx=(8, 12))
        self._toolbar_button(
            toolbar,
            "",
            "settings",
            self.open_settings_modal,
            "transparent",
            COLOR_UTILITY_HOVER,
            34,
            tooltip="基本設定",
        ).pack(side=RIGHT, padx=(4, 12), pady=10)
        ctk.CTkFrame(
            toolbar, width=1, height=22, fg_color=THEME.palette.toolbar_divider
        ).pack(side=RIGHT, padx=(4, 8), pady=16)
        self._toolbar_button(
            toolbar,
            "",
            "chevron_right",
            self.next_image,
            "transparent",
            COLOR_UTILITY_HOVER,
            34,
            tooltip="次の画像",
        ).pack(side=RIGHT, padx=2, pady=10)
        ctk.CTkLabel(
            toolbar,
            textvariable=self.image_count_var,
            width=66,
            anchor="center",
            font=UI_FONT_SMALL,
            text_color=THEME.palette.toolbar_muted,
        ).pack(side=RIGHT)
        self._toolbar_button(
            toolbar,
            "",
            "chevron_left",
            self.previous_image,
            "transparent",
            COLOR_UTILITY_HOVER,
            34,
            tooltip="前の画像",
        ).pack(side=RIGHT, padx=2, pady=10)

        canvas_frame = ctk.CTkFrame(
            canvas_area, corner_radius=0, fg_color=COLOR_CANVAS_PANEL
        )
        canvas_frame.pack(side=TOP, fill=BOTH, expand=True)
        self.canvas_frame = canvas_frame
        self.canvas = Canvas(canvas_frame, bg=COLOR_CANVAS_BG, highlightthickness=0)
        self.hbar = ctk.CTkScrollbar(
            canvas_frame,
            orientation=HORIZONTAL,
            command=self.canvas.xview,
            height=12,
            corner_radius=0,
            fg_color=COLOR_CANVAS_TOOLBAR,
            button_color=COLOR_SECONDARY,
            button_hover_color=COLOR_SECONDARY_HOVER,
        )
        self.vbar = ctk.CTkScrollbar(
            canvas_frame,
            orientation=VERTICAL,
            command=self.canvas.yview,
            width=12,
            corner_radius=0,
            fg_color=COLOR_CANVAS_TOOLBAR,
            button_color=COLOR_SECONDARY,
            button_hover_color=COLOR_SECONDARY_HOVER,
        )
        self.canvas.configure(
            xscrollcommand=self.hbar.set, yscrollcommand=self.vbar.set
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar.grid(row=1, column=0, sticky="ew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)
        self._set_canvas_scrollbars(False, False)

        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.canvas.bind("<Double-Button-1>", self.on_canvas_double_click)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<Control-MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<Motion>", self.on_canvas_motion)
        self.canvas.bind("<Button-3>", self.show_canvas_context_menu)
        self.canvas.bind("<Configure>", self.on_canvas_configure)

        status_bar = ctk.CTkFrame(
            canvas_area,
            height=THEME.layout.status_bar_height,
            corner_radius=0,
            fg_color=COLOR_CANVAS_TOOLBAR,
        )
        status_bar.pack(side=BOTTOM, fill="x")
        status_bar.pack_propagate(False)
        status_bar.grid_rowconfigure(0, weight=1)
        status_bar.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(
            status_bar,
            textvariable=self.status_var,
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=THEME.palette.toolbar_text,
        )
        self.status_label.grid(row=0, column=0, sticky="ew", padx=(12, 8))
        self.coordinate_status_label = ctk.CTkLabel(
            status_bar,
            textvariable=self.coordinate_status_var,
            anchor="e",
            font=UI_FONT_SMALL,
            text_color=THEME.palette.toolbar_muted,
        )
        self.coordinate_status_label.grid(
            row=0, column=1, sticky="e", padx=(8, 0)
        )
        self.coordinate_status_label.grid_remove()
        ctk.CTkLabel(
            status_bar,
            textvariable=self.progress_var,
            anchor="e",
            font=UI_FONT_SMALL,
            text_color=THEME.palette.toolbar_muted,
        ).grid(row=0, column=2, sticky="e", padx=(8, 12))

        side = ctk.CTkFrame(
            main,
            width=THEME.layout.side_panel_width,
            corner_radius=THEME.layout.panel_radius,
            fg_color=COLOR_SURFACE,
            border_width=1,
            border_color=THEME.palette.border_subtle,
        )
        side.pack(side=RIGHT, fill="y", padx=(12, 12), pady=12)
        side.pack_propagate(False)

        template_toolbar = ctk.CTkFrame(
            side,
            height=44,
            corner_radius=0,
            fg_color="transparent",
        )
        template_toolbar.pack(side=TOP, fill="x", padx=16, pady=(12, 4))
        template_toolbar.pack_propagate(False)
        template_toolbar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            template_toolbar,
            textvariable=self.template_var,
            anchor="w",
            justify="left",
            width=168,
            wraplength=168,
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.template_save_button = ctk.CTkButton(
            template_toolbar,
            text="保存",
            image=ICONS.get("save", 16, THEME.palette.on_color),
            compound="left",
            command=self.save_template,
            width=76,
            height=THEME.layout.entry_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            border_width=1,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
            text_color_disabled=COLOR_MUTED,
        )
        self.template_save_button.grid(row=0, column=1, padx=(0, 4))
        self.template_more_button = ctk.CTkButton(
            template_toolbar,
            text="",
            image=ICONS.get("more", 17, COLOR_MUTED),
            command=lambda: None,
            width=32,
            height=THEME.layout.entry_height,
            corner_radius=THEME.layout.control_radius,
            fg_color="transparent",
            hover_color=COLOR_UTILITY_HOVER,
            border_width=0,
        )
        self.template_more_button.configure(
            command=lambda button=self.template_more_button: (
                self.show_template_actions_menu(button)
            )
        )
        self.template_more_button.grid(row=0, column=2)
        add_tooltip(self.template_more_button, "テンプレート操作")
        self.template_toolbar = template_toolbar

        ctk.CTkSegmentedButton(
            side,
            values=[MODE_TEMPLATE, MODE_REVIEW, MODE_EXPORT],
            variable=self.mode_var,
            command=self._on_mode_change,
            height=THEME.layout.primary_button_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            selected_color=COLOR_PRIMARY,
            selected_hover_color=COLOR_PRIMARY_HOVER,
            unselected_color=THEME.palette.segmented_bg,
            unselected_hover_color=THEME.palette.segmented_hover,
            text_color=COLOR_TEXT,
        ).pack(side=TOP, fill="x", padx=16, pady=(4, 12))

        readiness_banner = ctk.CTkFrame(
            side,
            fg_color=COLOR_SURFACE_ALT,
            border_width=1,
            border_color=THEME.palette.warning,
            corner_radius=THEME.layout.control_radius,
        )
        readiness_banner.pack(side=TOP, fill="x", padx=16, pady=(0, 10))
        readiness_banner.grid_columnconfigure(0, weight=1)
        self.ocr_readiness_label = ctk.CTkLabel(
            readiness_banner,
            text="OCR未準備 — 範囲編集のみ利用できます",
            image=ICONS.get("alert_circle", 15, THEME.palette.warning),
            compound="left",
            anchor="w",
            justify="left",
            font=UI_FONT_SMALL,
            text_color=THEME.palette.warning,
            wraplength=235,
        )
        self.ocr_readiness_label.grid(
            row=0, column=0, sticky="ew", padx=(10, 6), pady=8
        )
        self.ocr_readiness_button = ctk.CTkButton(
            readiness_banner,
            text="準備",
            command=self.show_ocr_setup,
            width=54,
            height=28,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            border_width=1,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        )
        self.ocr_readiness_button.grid(
            row=0, column=1, padx=(0, 8), pady=6
        )
        self.ocr_readiness_banner = readiness_banner

        self.side_body = ctk.CTkFrame(side, corner_radius=0, fg_color=COLOR_SURFACE)
        self.side_body.pack(side=TOP, fill=BOTH, expand=True)
        self._render_side_body()
        self._build_loading_overlay()
        self._build_workflow_choice_overlay()
        self._build_ocr_setup_overlay()
        self._show_initial_overlay()

    def _toolbar_button(
        self,
        parent,
        text: str,
        icon: str,
        command,
        fg: str,
        hover: str,
        width: int,
        *,
        tooltip: str | None = None,
    ) -> ctk.CTkButton:
        button = ctk.CTkButton(
            parent,
            text=text,
            image=ICONS.get(icon, 18, THEME.palette.toolbar_text),
            compound="left",
            command=command,
            width=width,
            height=THEME.layout.button_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=fg,
            hover_color=hover,
        )
        if tooltip:
            add_tooltip(button, tooltip)
        return button

    def show_template_actions_menu(self, button: ctk.CTkButton) -> None:
        menu = Menu(
            self.root,
            tearoff=0,
            background=COLOR_SURFACE_ALT,
            foreground=COLOR_TEXT,
            activebackground=COLOR_PRIMARY,
            activeforeground=THEME.palette.on_color,
            disabledforeground=THEME.palette.disabled_fill,
            borderwidth=1,
            relief="flat",
        )
        menu.add_command(
            label="テンプレートを読み込む    Ctrl+O",
            command=self.load_template,
        )
        menu.add_command(
            label="名前を付けて保存    Ctrl+Shift+S",
            command=self.save_template_as,
            state="normal" if self.fields else "disabled",
        )
        menu.update_idletasks()
        x = (
            button.winfo_rootx()
            + button.winfo_width()
            - menu.winfo_reqwidth()
        )
        y = button.winfo_rooty() + button.winfo_height()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def show_export_actions_menu(self, button: ctk.CTkButton) -> None:
        menu = Menu(
            self.root,
            tearoff=0,
            background=COLOR_SURFACE_ALT,
            foreground=COLOR_TEXT,
            activebackground=COLOR_PRIMARY,
            activeforeground=THEME.palette.on_color,
            disabledforeground=THEME.palette.disabled_fill,
            borderwidth=1,
            relief="flat",
        )
        menu.add_command(
            label="CSV形式で保存…",
            command=self.export_to_csv,
        )
        menu.update_idletasks()
        x = button.winfo_rootx() + button.winfo_width() - menu.winfo_reqwidth()
        y = button.winfo_rooty() + button.winfo_height()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _build_loading_overlay(self) -> None:
        modal = ModalOverlay(
            self.root,
            width=360,
            backdrop_color=THEME.palette.modal_backdrop,
            backdrop_alpha=THEME.layout.modal_backdrop_alpha,
            surface_color=COLOR_SURFACE,
            border_color=THEME.palette.info,
            corner_radius=THEME.layout.panel_radius,
        )
        overlay = modal.panel
        overlay.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            overlay,
            textvariable=self.loading_var,
            anchor="w",
            justify="left",
            font=UI_FONT_BOLD,
            text_color=COLOR_TEXT,
            wraplength=276,
        ).grid(row=0, column=0, sticky="ew", padx=(22, 6), pady=(18, 10))
        self.loading_close_button = ctk.CTkButton(
            overlay,
            text="",
            image=ICONS.get("x", 16, COLOR_MUTED),
            command=self.confirm_cancel_current_operation,
            width=28,
            height=28,
            corner_radius=THEME.layout.control_radius,
            fg_color="transparent",
            hover_color=THEME.palette.modal_button_hover,
            border_width=0,
        )
        self.loading_close_button.grid(
            row=0, column=1, sticky="ne", padx=(0, 8), pady=(8, 0)
        )
        add_tooltip(self.loading_close_button, "処理をキャンセル")
        self.loading_progress = ctk.CTkProgressBar(
            overlay,
            mode="indeterminate",
            height=6,
            fg_color=COLOR_SECONDARY,
            progress_color=THEME.palette.info,
        )
        self.loading_progress.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=22, pady=(0, 10)
        )
        ctk.CTkLabel(
            overlay,
            textvariable=self.loading_note_var,
            anchor="center",
            justify="center",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
            wraplength=310,
        ).grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=22,
            pady=(0, 18),
        )
        modal.set_escape_handler(self.confirm_cancel_current_operation)
        modal.set_focus_order(
            [self.loading_close_button], default=self.loading_close_button
        )
        self.loading_modal = modal
        self.loading_overlay = overlay

    def _build_workflow_choice_overlay(self) -> None:
        modal = ModalOverlay(
            self.root,
            width=470,
            backdrop_color=THEME.palette.modal_backdrop,
            backdrop_alpha=THEME.layout.modal_backdrop_alpha,
            surface_color=COLOR_SURFACE,
            border_color=COLOR_BORDER,
            corner_radius=THEME.layout.panel_radius,
        )
        overlay = modal.panel
        overlay.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            overlay,
            text="開始方法を選択",
            image=ICONS.get("scan", 26, COLOR_PRIMARY),
            compound="top",
            font=UI_FONT_TITLE,
            text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="ew", padx=28, pady=(26, 8))
        close_button = ctk.CTkButton(
            overlay,
            text="",
            image=ICONS.get("x", 16, COLOR_MUTED),
            command=self.continue_without_workflow_choice,
            width=28,
            height=28,
            corner_radius=THEME.layout.control_radius,
            fg_color="transparent",
            hover_color=THEME.palette.modal_button_hover,
            border_width=0,
        )
        close_button.grid(
            row=0, column=0, sticky="ne", padx=(0, 12), pady=(12, 0)
        )
        add_tooltip(close_button, "開始方法を選ばずに進む")
        ctk.CTkLabel(
            overlay,
            text=self.app_config.copy.workflow_choice_note,
            justify="center",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
            wraplength=390,
        ).grid(row=1, column=0, sticky="ew", padx=28, pady=(0, 20))
        if self.profile.auto_detection is not None:
            primary_button = ctk.CTkButton(
                overlay,
                text=self.app_config.copy.auto_detection_action,
                image=ICONS.get("scan", 18, THEME.palette.on_color),
                compound="left",
                command=self.start_auto_detection_workflow,
                height=THEME.layout.primary_button_height,
                corner_radius=THEME.layout.control_radius,
                font=UI_FONT_BOLD,
                fg_color=COLOR_PRIMARY,
                hover_color=COLOR_PRIMARY_HOVER,
            )
            primary_button.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 8))
            secondary_button = ctk.CTkButton(
                overlay,
                text="新規テンプレートを作成",
                image=ICONS.get("plus", 17, COLOR_TEXT),
                compound="left",
                command=self.start_new_template_workflow,
                height=THEME.layout.primary_button_height,
                corner_radius=THEME.layout.control_radius,
                font=UI_FONT_SMALL,
                fg_color=COLOR_SECONDARY,
                hover_color=COLOR_SECONDARY_HOVER,
                border_width=1,
                border_color=COLOR_BORDER,
            )
            secondary_button.grid(
                row=3, column=0, sticky="ew", padx=28, pady=(0, 26)
            )
            focus_order = [primary_button, secondary_button, close_button]
        else:
            primary_button = ctk.CTkButton(
                overlay,
                text="新規テンプレートを作成",
                image=ICONS.get("plus", 18, THEME.palette.on_color),
                compound="left",
                command=self.start_new_template_workflow,
                height=THEME.layout.primary_button_height,
                corner_radius=THEME.layout.control_radius,
                font=UI_FONT_BOLD,
                fg_color=COLOR_PRIMARY,
                hover_color=COLOR_PRIMARY_HOVER,
            )
            primary_button.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 26))
            focus_order = [primary_button, close_button]

        modal.set_focus_order(
            focus_order,
            default=primary_button,
        )
        modal.set_default_action(primary_button)
        modal.set_escape_handler(self.continue_without_workflow_choice)
        self.workflow_choice_modal = modal
        self.workflow_choice_overlay = overlay
        self.workflow_choice_primary_button = primary_button

    def _build_ocr_setup_overlay(self) -> None:
        modal = ModalOverlay(
            self.root,
            width=470,
            backdrop_color=THEME.palette.modal_backdrop,
            backdrop_alpha=THEME.layout.modal_backdrop_alpha,
            surface_color=COLOR_SURFACE,
            border_color=THEME.palette.warning,
            corner_radius=THEME.layout.panel_radius,
        )
        panel = modal.panel
        panel.grid_columnconfigure(0, weight=1)
        self.ocr_setup_title_label = ctk.CTkLabel(
            panel,
            text="OCR認識モデルをダウンロード",
            image=ICONS.get("scan", 26, COLOR_PRIMARY),
            compound="top",
            font=UI_FONT_TITLE,
            text_color=COLOR_TEXT,
        )
        self.ocr_setup_title_label.grid(
            row=0, column=0, sticky="ew", padx=28, pady=(26, 8)
        )
        self.ocr_setup_subtitle_label = ctk.CTkLabel(
            panel,
            text=(
                "PaddleOCRでの文字認識には、実行環境と認識モデルが必要です。\n"
                "この端末では、認識モデルがまだ準備されていません。"
            ),
            justify="center",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
            wraplength=390,
        )
        self.ocr_setup_subtitle_label.grid(
            row=1, column=0, sticky="ew", padx=28, pady=(0, 16)
        )
        status_frame = ctk.CTkFrame(
            panel,
            fg_color=COLOR_SURFACE_ALT,
            border_width=1,
            border_color=COLOR_BORDER,
            corner_radius=THEME.layout.control_radius,
        )
        status_frame.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 18))
        status_frame.grid_columnconfigure(0, weight=1)
        self.ocr_setup_status_label = ctk.CTkLabel(
            status_frame,
            text="",
            image=ICONS.get("alert_circle", 17, THEME.palette.warning),
            compound="left",
            anchor="w",
            font=UI_FONT_BOLD,
            text_color=THEME.palette.warning,
        )
        self.ocr_setup_status_label.grid(
            row=0, column=0, sticky="ew", padx=14, pady=(12, 4)
        )
        self.ocr_setup_note_label = ctk.CTkLabel(
            status_frame,
            text="",
            anchor="w",
            justify="left",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
            wraplength=370,
        )
        self.ocr_setup_note_label.grid(
            row=1, column=0, sticky="ew", padx=14, pady=(0, 12)
        )
        self.ocr_setup_progress = ctk.CTkProgressBar(
            status_frame,
            height=4,
            corner_radius=2,
            fg_color=THEME.palette.border_subtle,
            progress_color=THEME.palette.info,
            mode="indeterminate",
        )
        self.ocr_setup_progress.grid(
            row=2, column=0, sticky="ew", padx=14, pady=(2, 12)
        )
        self.ocr_setup_progress.grid_remove()
        self.ocr_setup_primary_button = ctk.CTkButton(
            panel,
            text="ダウンロードして準備",
            image=ICONS.get("refresh", 17, THEME.palette.on_color),
            compound="left",
            command=self.prepare_ocr_environment,
            height=THEME.layout.primary_button_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_BOLD,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_PRIMARY_HOVER,
        )
        self.ocr_setup_primary_button.grid(
            row=3, column=0, sticky="ew", padx=28, pady=(0, 8)
        )
        self.ocr_setup_secondary_button = ctk.CTkButton(
            panel,
            text="今は準備せず、範囲編集へ",
            command=self.defer_ocr_setup,
            height=THEME.layout.primary_button_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            border_width=1,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        )
        self.ocr_setup_secondary_button.grid(
            row=4, column=0, sticky="ew", padx=28, pady=(0, 8)
        )
        self.ocr_setup_settings_button = ctk.CTkButton(
            panel,
            text="モデル保存先を変更",
            command=self.open_ocr_environment_settings,
            height=30,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color="transparent",
            hover_color=COLOR_UTILITY_HOVER,
            border_width=0,
            text_color=COLOR_MUTED,
        )
        self.ocr_setup_settings_button.grid(row=5, column=0, pady=(0, 20))
        modal.set_focus_order(
            [
                self.ocr_setup_primary_button,
                self.ocr_setup_secondary_button,
                self.ocr_setup_settings_button,
            ],
            default=self.ocr_setup_primary_button,
        )
        modal.set_escape_handler(self.defer_ocr_setup)
        modal.set_default_action(self.ocr_setup_primary_button)
        self.ocr_setup_modal = modal

    def _show_initial_overlay(self) -> None:
        status = self.ocr_environment.quick_status()
        self._refresh_ocr_readiness_banner(status)
        if status.ready:
            self._show_workflow_choice()
        else:
            self.show_ocr_setup()

    def _show_loading(
        self,
        message: str,
        *,
        note: str | None = None,
        progress: float | None = None,
    ) -> None:
        self.loading_var.set(message)
        self.loading_note_var.set(
            note or "処理中の項目が完了するまでお待ちください。"
        )
        if self.loading_close_button is not None:
            self.loading_close_button.configure(state="normal")
        if self.loading_modal is not None:
            self.loading_modal.show(focus=self.loading_close_button)
        if self.loading_progress is not None:
            if progress is None:
                self.loading_progress.configure(mode="indeterminate")
                self.loading_progress.start()
            else:
                self.loading_progress.stop()
                self.loading_progress.configure(mode="determinate")
                self.loading_progress.set(max(0.0, min(1.0, progress)))
        self.root.update_idletasks()

    def _update_loading(
        self,
        message: str,
        *,
        note: str | None = None,
        progress: float | None = None,
    ) -> None:
        self.loading_var.set(message)
        if note is not None:
            self.loading_note_var.set(note)
        if progress is not None and self.loading_progress is not None:
            self.loading_progress.set(max(0.0, min(1.0, progress)))
        if self.loading_modal is not None and self.loading_modal.visible:
            # Keep the progress surface above the application while frequent
            # queue/status redraws are performed during an export.
            self.loading_modal.show(focus=self.loading_close_button)
        self.root.update_idletasks()

    def _hide_loading(self) -> None:
        if self.loading_progress is not None:
            self.loading_progress.stop()
        if self.loading_modal is not None:
            self.loading_modal.hide()
        self.root.update_idletasks()

    def _run_background(
        self,
        loading_message: str,
        work,
        on_success,
        on_error=None,
        on_finally=None,
        on_cancel=None,
        *,
        show_loading_overlay: bool = True,
        loading_note: str | None = None,
        loading_progress: float | None = None,
    ) -> None:
        if self.busy:
            self.status_var.set("処理中です。完了までお待ちください。")
            return
        self.busy = True
        self._operation_cancel_event = threading.Event()
        if show_loading_overlay:
            self._show_loading(
                loading_message,
                note=loading_note,
                progress=loading_progress,
            )
        logger.info("Background operation started | operation=%s", loading_message)

        def finish_common() -> bool:
            close_after_cancel = self._close_after_cancel
            self._close_after_cancel = False
            self.busy = False
            self._operation_cancel_event = None
            if show_loading_overlay:
                self._hide_loading()
            if on_finally is not None:
                on_finally()
            return close_after_cancel

        def finish_result(callback, *args) -> None:
            close_after_cancel = finish_common()
            try:
                callback(*args)
            finally:
                if close_after_cancel:
                    self.request_close()

        def finish_success(result) -> None:
            logger.info("Background operation completed | operation=%s", loading_message)
            finish_result(on_success, result)

        def finish_error(error: Exception) -> None:
            def report_error() -> None:
                if on_error is not None:
                    on_error(error)
                else:
                    self.dialogs.showerror("処理エラー", str(error))

            finish_result(report_error)

        def finish_cancelled() -> None:
            def report_cancelled() -> None:
                if on_cancel is not None:
                    on_cancel()
                else:
                    self.status_var.set("処理をキャンセルしました。")

            logger.info("Background operation cancelled | operation=%s", loading_message)
            finish_result(report_cancelled)

        def worker() -> None:
            try:
                result = work()
            except OperationCancelled:
                self.root.after(0, finish_cancelled)
            except Exception as exc:
                logger.exception(
                    "Background operation failed | operation=%s", loading_message
                )
                self.root.after(0, lambda error=exc: finish_error(error))
            else:
                self.root.after(0, lambda value=result: finish_success(value))

        threading.Thread(target=worker, daemon=True).start()

    def _report_callback_exception(self, exc_type, exc_value, exc_traceback) -> None:
        logger.critical(
            "Unhandled UI callback exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        try:
            self.dialogs.showerror(
                "予期しないエラー",
                "処理中にエラーが発生しました。\n"
                "アプリを再起動しても解決しない場合は、OCR環境設定から診断ログを確認してください。",
            )
        except Exception:
            logger.exception("Failed to show the unexpected-error dialog")

    def _is_operation_cancelled(self) -> bool:
        event = self._operation_cancel_event
        return event is not None and event.is_set()

    def _raise_if_operation_cancelled(self) -> None:
        raise_if_cancelled(self._is_operation_cancelled)

    def confirm_cancel_current_operation(self) -> None:
        if not self.busy or self._is_operation_cancelled():
            return
        detail = (
            "認識モデルの準備・確認処理を終了します。\n"
            "完了していないモデルは準備済みとして記録されません。"
            if self._ocr_setup_operation
            else (
                "現在処理中の項目が終わり次第、安全に停止します。"
                "\nファイル出力中の場合、未完了の内容は保存されません。"
            )
        )
        if not self.dialogs.askyesno(
            "処理をキャンセル",
            f"現在の処理をキャンセルしますか？\n\n{detail}",
            yes_text="処理をキャンセル",
            no_text="戻る",
            destructive=True,
        ):
            return
        self.cancel_current_operation()

    def cancel_current_operation(self) -> None:
        event = self._operation_cancel_event
        if not self.busy or event is None or event.is_set():
            return
        event.set()
        if self._ocr_setup_operation:
            self.status_var.set("OCRの準備をキャンセルしています。")
            self.loading_note_var.set("モデル準備用プロセスを終了しています。")
            if self.ocr_setup_note_label is not None:
                self.ocr_setup_note_label.configure(
                    text="モデル準備用プロセスを終了しています。"
                )
        else:
            self.status_var.set("キャンセルを受け付けました。安全に停止しています。")
            self.loading_note_var.set("現在処理中の項目が終わるまでお待ちください。")
        if self.loading_close_button is not None:
            self.loading_close_button.configure(state="disabled")

    def _bind_shortcuts(self) -> None:
        shortcuts = (
            ("<Control-Left>", self.previous_image),
            ("<Control-Right>", self.next_image),
            ("<Control-s>", self.save_template),
            ("<Control-Shift-S>", self.save_template_as),
            ("<Control-o>", self.load_template),
            ("<Control-Return>", self.export_to_excel),
            ("<Alt-Up>", self.move_selected_field_up),
            ("<Alt-Down>", self.move_selected_field_down),
            ("<F2>", self.edit_selected_field_name),
            ("<Escape>", self.undo_last_action),
            ("<Control-z>", self.undo_last_action),
            ("<Control-Z>", self.undo_last_action),
        )
        for sequence, command in shortcuts:
            self.root.bind_all(
                sequence,
                lambda event, handler=command: self._dispatch_shortcut(
                    handler, event
                ),
            )

    def _dispatch_shortcut(self, command, event):
        if getattr(self, "busy", False):
            return "break"
        ocr_setup_modal = getattr(self, "ocr_setup_modal", None)
        if ocr_setup_modal is not None and ocr_setup_modal.visible:
            return "break"
        if not self.workflow_started:
            self._show_workflow_choice()
            return "break"
        return command(event)

    def _bind_dirty_tracking(self) -> None:
        variables = [
            self.lang_var,
            self.export_sheet_var,
            self.export_write_mode_var,
            self.export_start_cell_var,
            self.export_include_filename_var,
            self.export_include_header_var,
            self.export_layout_var,
        ]
        for variable in variables:
            variable.trace_add("write", self._on_template_setting_changed)

    def _on_template_setting_changed(self, *_args) -> None:
        self._mark_dirty()

    @contextmanager
    def _without_dirty_tracking(self):
        previous = self._dirty_tracking_suspended
        self._dirty_tracking_suspended = True
        try:
            yield
        finally:
            self._dirty_tracking_suspended = previous

    def _mark_dirty(self) -> None:
        if self._dirty_tracking_suspended or self.dirty:
            return
        self.dirty = True
        self._update_dirty_indicators()

    def _mark_clean(self) -> None:
        self.dirty = False
        self._update_dirty_indicators()

    def _update_dirty_indicators(self) -> None:
        suffix = " *" if self.dirty else ""
        self.root.title(f"{self.app_config.app_title}{suffix}")
        template_name = (
            self.template_path.name
            if self.template_path
            else THEME.copy.new_template_label
        )
        self.template_var.set(f"{template_name}{suffix}")
        self._update_template_toolbar_state()

    def _update_template_toolbar_state(self) -> None:
        button = self.template_save_button
        if button is None or not button.winfo_exists():
            return
        has_fields = bool(self.fields)
        button.configure(
            state="normal" if has_fields else "disabled",
            border_width=0 if self.dirty and has_fields else 1,
            fg_color=(
                COLOR_PRIMARY
                if self.dirty and has_fields
                else COLOR_SECONDARY
            ),
            hover_color=(
                COLOR_PRIMARY_HOVER
                if self.dirty and has_fields
                else COLOR_SECONDARY_HOVER
            ),
            border_color=(
                COLOR_PRIMARY if self.dirty and has_fields else COLOR_BORDER
            ),
        )

    def _confirm_unsaved_changes(self, action: str) -> bool:
        if not self.dirty:
            return True
        response = self.dialogs.askyesnocancel(
            "未保存の変更",
            f"{action}前にテンプレートの変更を保存しますか。\n\n"
            "保存しない場合、変更内容は破棄されます。",
            yes_text="保存",
            no_text="保存しない",
            no_destructive=True,
        )
        if response is None:
            return False
        if response:
            return self.save_template()
        return True

    def request_close(self) -> None:
        if self.busy:
            if self._close_after_cancel:
                self.dialogs.showinfo(
                    "停止待機中",
                    "現在処理中の項目が終わり次第、終了確認へ進みます。",
                )
                return
            if not self.dialogs.askyesno(
                "処理中です",
                "処理をキャンセルして終了しますか？\n\n"
                "現在処理中の項目が終わり次第、安全に停止します。"
                "\nファイル出力中の場合、未完了の内容は保存されません。",
                yes_text="キャンセルして終了",
                no_text="戻る",
                destructive=True,
            ):
                return
            self._close_after_cancel = True
            self.cancel_current_operation()
            return
        if not self._confirm_unsaved_changes("終了する"):
            return
        self.root.destroy()

    def _on_mode_change(self, _value: str | None = None) -> None:
        if self.mode_var.get() != MODE_TEMPLATE:
            had_edit_preview = self.canvas_edit_state is not None
            self._clear_canvas_pointer_interaction()
            self.canvas.configure(cursor="arrow")
            if had_edit_preview:
                self.redraw()
        self._update_coordinate_status_display()
        self._schedule_side_body_render()

    def _render_side_body(self) -> None:
        self._side_render_job = None
        if self.side_body is None:
            return
        previous_body = self.side_body
        replacement_body = ctk.CTkFrame(
            previous_body.master, corner_radius=0, fg_color=COLOR_SURFACE
        )
        self.side_body = replacement_body
        self.field_row_by_index = {}
        self.field_drag_handle_by_index = {}
        self.set_drop_section_by_id = {}
        self.set_drag_target_id = None
        self.review_order_label_by_index = {}
        self.review_detail_host = None
        self.review_result_scroller = None
        self.correction_rules_host = None
        self.template_fields_scroller = None
        mode = self.mode_var.get()
        try:
            if mode == MODE_TEMPLATE:
                self._render_template_mode()
            elif mode == MODE_REVIEW:
                self._render_review_mode()
            else:
                self._render_export_mode()
        except Exception:
            replacement_body.destroy()
            self.side_body = previous_body
            raise
        previous_body.pack_forget()
        replacement_body.pack(side=TOP, fill=BOTH, expand=True)
        previous_body.destroy()
        self._update_coordinate_status_display()

    def _schedule_side_body_render(self) -> None:
        if self._side_render_job is not None:
            try:
                self.root.after_cancel(self._side_render_job)
            except Exception:
                pass
        self._side_render_job = self.root.after_idle(self._render_side_body)

    def _render_template_mode(self) -> None:
        body = self.side_body
        assert body is not None
        self.field_check_vars = []
        self.field_row_widgets = []
        self.field_row_by_index = {}
        self.field_drag_handle_by_index = {}
        self.set_drop_section_by_id = {}
        layout_row = ctk.CTkFrame(body, fg_color="transparent")
        layout_row.pack(fill="x", padx=16, pady=(2, 8))
        layout_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            layout_row,
            text="Excelの行単位",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        layout_menu = ctk.CTkOptionMenu(
            layout_row,
            values=list(EXPORT_LAYOUT_DISPLAY.values()),
            height=THEME.layout.entry_height,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_PRIMARY_HOVER,
            dropdown_fg_color=COLOR_SURFACE_ALT,
            dropdown_hover_color=COLOR_SECONDARY_HOVER,
            text_color=COLOR_TEXT,
        )
        layout_menu.configure(
            command=lambda display: self._set_export_layout(
                EXPORT_LAYOUT_BY_DISPLAY[display], layout_menu
            )
        )
        layout_menu.set(EXPORT_LAYOUT_DISPLAY[self.export_layout_var.get()])
        layout_menu.grid(row=0, column=1, sticky="ew")
        if self.export_layout_var.get() == EXPORT_LAYOUT_SET:
            preset_row = ctk.CTkFrame(body, fg_color="transparent")
            preset_row.pack(fill="x", padx=16, pady=(0, 8))
            preset_row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(
                preset_row,
                text="項目の組み合わせ",
                anchor="w",
                font=UI_FONT_SMALL,
                text_color=COLOR_MUTED,
            ).grid(row=0, column=0, sticky="w", padx=(0, 8))
            preset_menu = ctk.CTkOptionMenu(
                preset_row,
                values=list(self.profile.set_preset_display.values()),
                height=THEME.layout.entry_height,
                font=UI_FONT_SMALL,
                fg_color=COLOR_SECONDARY,
                button_color=COLOR_PRIMARY,
                button_hover_color=COLOR_PRIMARY_HOVER,
                dropdown_fg_color=COLOR_SURFACE_ALT,
                dropdown_hover_color=COLOR_SECONDARY_HOVER,
                text_color=COLOR_TEXT,
            )
            preset_menu.configure(
                command=lambda value: self._set_set_preset(value, preset_menu)
            )
            preset_menu.set(self.set_preset_var.get())
            preset_menu.grid(row=0, column=1, sticky="ew")
            ctk.CTkLabel(
                body,
                text=THEME.copy.set_layout_note,
                anchor="w",
                justify="left",
                font=UI_FONT_SMALL,
                text_color=COLOR_MUTED,
                wraplength=350,
            ).pack(fill="x", padx=16, pady=(0, 6))
            set_error = (
                set_validation_error(self._enabled_fields(), self.set_definition)
                if self.fields
                else None
            )
            if set_error:
                ctk.CTkLabel(
                    body,
                    text=set_error,
                    image=ICONS.get("alert_circle", 16, COLOR_DANGER),
                    compound="left",
                    anchor="w",
                    justify="left",
                    font=UI_FONT_SMALL,
                    text_color=COLOR_DANGER,
                    wraplength=320,
                ).pack(fill="x", padx=16, pady=(0, 6))
            action = ctk.CTkFrame(body, fg_color="transparent")
            action.pack(fill="x", padx=16, pady=(0, 8))
            action.grid_columnconfigure(0, weight=1)
            pending = self.pending_set_id is not None
            button_text = self._set_creation_button_text()
            ctk.CTkButton(
                action,
                text=button_text,
                image=ICONS.get("plus", 16, THEME.palette.on_color),
                compound="left",
                command=self.begin_set_creation,
                height=THEME.layout.entry_height,
                corner_radius=THEME.layout.control_radius,
                font=UI_FONT_SMALL,
                fg_color=COLOR_PRIMARY,
                hover_color=COLOR_PRIMARY_HOVER,
            ).grid(row=0, column=0, sticky="ew")
            if pending:
                ctk.CTkButton(
                    action,
                    text="中止",
                    image=ICONS.get("x", 15, COLOR_TEXT),
                    compound="left",
                    command=self.cancel_set_creation,
                    width=68,
                    height=THEME.layout.entry_height,
                    corner_radius=THEME.layout.control_radius,
                    font=UI_FONT_SMALL,
                    fg_color=COLOR_SECONDARY,
                    hover_color=COLOR_SECONDARY_HOVER,
                    border_width=1,
                    border_color=COLOR_BORDER,
                ).grid(row=0, column=1, padx=(6, 0))
        else:
            ctk.CTkLabel(
                body,
                text=THEME.copy.image_layout_note,
                anchor="w",
                justify="left",
                font=UI_FONT_SMALL,
                text_color=COLOR_MUTED,
                wraplength=350,
            ).pack(fill="x", padx=16, pady=(0, 6))

        scroller = AutoHideScrollableFrame(
            body,
            corner_radius=THEME.layout.panel_radius,
            fg_color=COLOR_SURFACE_ALT,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        scroller.pack(side=TOP, fill=BOTH, expand=True, padx=16, pady=(0, 10))
        self.template_fields_scroller = scroller
        if (
            not self.fields
            and self.export_layout_var.get() != EXPORT_LAYOUT_SET
        ):
            self._empty_state(
                scroller,
                "項目がありません",
                "画像上で範囲をドラッグ",
                THEME.copy.saved_template_hint,
                icon="scan",
            )
        elif self.export_layout_var.get() == EXPORT_LAYOUT_SET:
            self._render_set_fields(scroller)
        else:
            for idx, field in enumerate(self.fields):
                self._field_row(scroller, idx)

    def _set_export_layout(
        self, value: str, menu: ctk.CTkOptionMenu | None = None
    ) -> None:
        current_layout = self.export_layout_var.get()
        if (
            value == EXPORT_LAYOUT_IMAGE_ROW
            and current_layout != value
            and self.auto_detection_enabled
            and not self._confirm_fixed_range_switch()
        ):
            if menu is not None:
                menu.set(EXPORT_LAYOUT_DISPLAY[current_layout])
            self.status_var.set("自動検出を継続します。")
            return
        if current_layout != value:
            self.export_layout_var.set(value)
        if value == EXPORT_LAYOUT_SET:
            self.empty_set_ids.add(1)
            self._assign_fields_to_current_set_definition(reassign_all=False)
            self._prepare_initial_set_creation()
            self.status_var.set(
                "セット1を用意し、既存項目を割り当てました。"
                if self.fields
                else "セット1を用意しました。最初の範囲をドラッグしてください。"
            )
        else:
            fixed_auto_detection_ranges = self.auto_detection_enabled
            if fixed_auto_detection_ranges:
                self._adopt_current_image_as_coordinate_source()
            self.pending_set_id = None
            self.pending_slot_key = ""
            self.empty_set_ids.clear()
            self.auto_detection_enabled = False
            self.auto_detection_base_fields = []
            if fixed_auto_detection_ranges:
                self.status_var.set(
                    "現在画像の検出範囲を、画像単位の固定範囲として引き継ぎました。"
                )
        self._schedule_side_body_render()
        self.redraw()

    def _confirm_fixed_range_switch(self) -> bool:
        image_name = self.image_path.name if self.image_path else "現在表示中の画像"
        return self.dialogs.askyesno(
            "固定範囲へ切り替え",
            f"{image_name} の検出範囲を固定範囲として引き継ぎます。\n\n"
            "切り替え後は、画像ごとの自動検出を行いません。",
            yes_text="固定範囲に切り替える",
            no_text="自動検出を続ける",
            kind="warning",
        )

    def _assign_fields_to_current_set_definition(
        self, reassign_all: bool
    ) -> bool:
        columns = self.set_definition.columns
        if not columns or not self.fields:
            return False
        allowed = set(self.set_definition.slot_keys)
        occupied_by_set: dict[int, set[str]] = {}
        if not reassign_all:
            for field in self.fields:
                if field.set_id > 0 and field.slot_key in allowed:
                    occupied_by_set.setdefault(field.set_id, set()).add(field.slot_key)

        changed = False
        overflow_position_by_set: dict[int, int] = {}
        for field in self.fields:
            if (
                not reassign_all
                and field.set_id > 0
                and field.slot_key in allowed
            ):
                continue
            set_id = field.set_id if field.set_id > 0 else 1
            occupied = occupied_by_set.setdefault(set_id, set())
            matching = next(
                (
                    column
                    for column in columns
                    if column.key not in occupied and column.label in field.name
                ),
                None,
            )
            column = matching or next(
                (column for column in columns if column.key not in occupied),
                None,
            )
            if column is None:
                position = overflow_position_by_set.get(set_id, 0)
                column = columns[position % len(columns)]
                overflow_position_by_set[set_id] = position + 1
            if (
                field.set_id != set_id
                or field.slot_key != column.key
                or field.ocr_line_split != column.ocr_line_split
            ):
                changed = True
            field.set_id = set_id
            field.slot_key = column.key
            field.ocr_line_split = column.ocr_line_split
            occupied.add(column.key)

        assigned_ids = {field.set_id for field in self.fields if field.set_id > 0}
        self.empty_set_ids.difference_update(assigned_ids)
        if changed:
            self.current_results = [""] * len(self.fields)
            self.current_raw_results = [""] * len(self.fields)
        return changed

    def _prepare_initial_set_creation(self) -> None:
        has_set_one_fields = any(field.set_id == 1 for field in self.fields)
        if has_set_one_fields:
            self.empty_set_ids.discard(1)
        else:
            self.empty_set_ids.add(1)
        slots_in_set_one = {
            field.slot_key
            for field in self.fields
            if field.set_id == 1
            and field.slot_key in self.set_definition.slot_keys
        }
        missing = next(
            (
                column.key
                for column in self.set_definition.columns
                if column.key not in slots_in_set_one
            ),
            "",
        )
        if missing:
            self.pending_set_id = 1
            self.pending_slot_key = missing
        else:
            self.pending_set_id = None
            self.pending_slot_key = ""

    def _set_set_preset(
        self, display: str, menu: ctk.CTkOptionMenu | None = None
    ) -> None:
        preset = next(
            (
                key
                for key, label in self.profile.set_preset_display.items()
                if label == display
            ),
            self.set_definition.preset,
        )
        if preset == self.set_definition.preset:
            return
        assigned = any(field.set_id > 0 or field.slot_key for field in self.fields)
        if assigned and not self.dialogs.askyesno(
            "項目の組み合わせを変更",
            "項目の組み合わせを変更すると、既存項目を新しい構成へ再割り当てします。変更しますか？",
            yes_text="変更する",
            no_text="戻る",
        ):
            current_display = self.profile.set_preset_display[
                self.set_definition.preset
            ]
            self.set_preset_var.set(current_display)
            if menu is not None:
                menu.set(current_display)
            return
        self.set_definition = self.profile.set_definition(preset)
        self.set_preset_var.set(self.profile.set_preset_display[preset])
        if menu is not None:
            menu.set(self.profile.set_preset_display[preset])
        self.auto_detection_enabled = False
        self.auto_detection_base_fields = []
        self.current_detection_layout = ""
        self.current_detection_reason = ""
        self.current_detection_count = 0
        if assigned:
            self._assign_fields_to_current_set_definition(reassign_all=True)
        self._prepare_initial_set_creation()
        self._mark_dirty()
        self.status_var.set(
            f"項目の組み合わせを「{self.set_definition.name}」に変更しました。"
        )
        self._schedule_side_body_render()
        self.redraw()

    def _set_ids(self) -> list[int]:
        set_ids = {field.set_id for field in self.fields if field.set_id > 0}
        set_ids.update(getattr(self, "empty_set_ids", set()))
        if self.pending_set_id is not None:
            set_ids.add(self.pending_set_id)
        return sorted(set_ids)

    def _next_set_id(self) -> int:
        set_ids = self._set_ids()
        return (set_ids[-1] + 1) if set_ids else 1

    def _set_creation_button_text(self) -> str:
        if self.pending_set_id is None:
            return "セットを追加"
        slot_label = self.set_definition.slot_label(self.pending_slot_key)
        return f"セット {self.pending_set_id}: {slot_label}範囲をプレビュー上でドラッグ"

    def begin_set_creation(self) -> None:
        if self.pending_set_id is None:
            incomplete = next(
                (
                    (set_id, column.key)
                    for set_id in self._set_ids()
                    for column in self.set_definition.columns
                    if not any(
                        field.set_id == set_id and field.slot_key == column.key
                        for field in self.fields
                    )
                ),
                None,
            )
            if incomplete is None:
                self.pending_set_id = self._next_set_id()
                self.pending_slot_key = self.set_definition.columns[0].key
                self.empty_set_ids.add(self.pending_set_id)
            else:
                self.pending_set_id, self.pending_slot_key = incomplete
        slot_label = self.set_definition.slot_label(self.pending_slot_key)
        self.status_var.set(
            f"セット {self.pending_set_id} の{slot_label}範囲をプレビュー上でドラッグしてください。"
        )
        self._schedule_side_body_render()

    def cancel_set_creation(self) -> None:
        self.pending_set_id = None
        self.pending_slot_key = ""
        self.status_var.set("セットの追加を中止しました。")
        self._schedule_side_body_render()

    def _render_set_fields(self, parent) -> None:
        set_ids = self._set_ids()
        for position, set_id in enumerate(set_ids):
            section = self._set_drop_section(parent, set_id)
            section.pack(fill="x", padx=7, pady=(7 if position == 0 else 10, 0))
            header = ctk.CTkFrame(section, fg_color="transparent")
            header.pack(fill="x", padx=7, pady=(5, 2))
            header.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                header,
                text=f"セット {set_id}",
                anchor="w",
                font=UI_FONT_BOLD,
                text_color=COLOR_TEXT,
            ).grid(row=0, column=0, sticky="ew")
            up_button = ctk.CTkButton(
                header,
                text="",
                image=ICONS.get("arrow_up", 15, COLOR_MUTED),
                command=lambda value=set_id: self.move_set(value, -1),
                width=30,
                height=28,
                state="normal" if position > 0 else "disabled",
                corner_radius=THEME.layout.control_radius,
                fg_color="transparent",
                hover_color=COLOR_UTILITY_HOVER,
                border_width=0,
            )
            up_button.grid(row=0, column=1, padx=(4, 2))
            add_tooltip(up_button, "セットを上へ")
            down_button = ctk.CTkButton(
                header,
                text="",
                image=ICONS.get("arrow_down", 15, COLOR_MUTED),
                command=lambda value=set_id: self.move_set(value, 1),
                width=30,
                height=28,
                state="normal" if position < len(set_ids) - 1 else "disabled",
                corner_radius=THEME.layout.control_radius,
                fg_color="transparent",
                hover_color=COLOR_UTILITY_HOVER,
                border_width=0,
            )
            down_button.grid(row=0, column=2, padx=(2, 0))
            add_tooltip(down_button, "セットを下へ")
            indexes = [
                idx for idx, field in enumerate(self.fields) if field.set_id == set_id
            ]
            indexes.sort(
                key=lambda idx: (
                    (
                        list(self.set_definition.allowed_slot_keys()).index(
                            self.fields[idx].slot_key
                        )
                        if self.fields[idx].slot_key
                        in self.set_definition.allowed_slot_keys()
                        else len(self.set_definition.allowed_slot_keys())
                    ),
                    idx,
                )
            )
            for idx in indexes:
                self._set_field_row(section, idx)
            if not indexes:
                ctk.CTkLabel(
                    section,
                    text="プレビュー上で最初の範囲をドラッグしてください。",
                    anchor="w",
                    font=UI_FONT_SMALL,
                    text_color=COLOR_MUTED,
                ).pack(fill="x", padx=8, pady=(4, 10))

        unassigned = [
            idx for idx, field in enumerate(self.fields) if field.set_id <= 0
        ]
        section = self._set_drop_section(parent, 0)
        section.pack(fill="x", padx=7, pady=(10, 7))
        ctk.CTkLabel(
            section,
            text="未割当",
            anchor="w",
            font=UI_FONT_BOLD,
            text_color=COLOR_DANGER if unassigned else COLOR_MUTED,
        ).pack(fill="x", padx=7, pady=(5, 2))
        if unassigned:
            for idx in unassigned:
                self._set_field_row(section, idx)
        else:
            ctk.CTkLabel(
                section,
                text="セットから外す場合はここへドラッグ",
                anchor="w",
                font=UI_FONT_SMALL,
                text_color=COLOR_MUTED,
            ).pack(fill="x", padx=8, pady=(4, 10))

    def _set_drop_section(self, parent, set_id: int) -> ctk.CTkFrame:
        section = ctk.CTkFrame(
            parent,
            corner_radius=THEME.layout.panel_radius,
            fg_color="transparent",
            border_width=1,
            border_color=COLOR_SURFACE_ALT,
        )
        self.set_drop_section_by_id[set_id] = section
        return section

    def move_set(self, set_id: int, direction: int) -> None:
        set_ids = self._set_ids()
        if set_id not in set_ids:
            return
        position = set_ids.index(set_id)
        target_position = position + direction
        if not (0 <= target_position < len(set_ids)):
            return
        other_id = set_ids[target_position]
        for field in self.fields:
            if field.set_id == set_id:
                field.set_id = other_id
            elif field.set_id == other_id:
                field.set_id = set_id
        remapped_empty_ids: set[int] = set()
        for value in self.empty_set_ids:
            if value == set_id:
                remapped_empty_ids.add(other_id)
            elif value == other_id:
                remapped_empty_ids.add(set_id)
            else:
                remapped_empty_ids.add(value)
        self.empty_set_ids = remapped_empty_ids
        self._mark_dirty()
        self.status_var.set("セットの出力順を変更しました。")
        self._render_side_body()
        self.redraw()

    def _render_review_mode(self) -> None:
        side_body = self.side_body
        assert side_body is not None
        body = ctk.CTkFrame(side_body, corner_radius=0, fg_color=COLOR_SURFACE)
        body.pack(fill=BOTH, expand=True)

        scroller = AutoHideScrollableFrame(
            body,
            height=220,
            corner_radius=THEME.layout.panel_radius,
            fg_color=COLOR_SURFACE_ALT,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        self.review_result_scroller = scroller
        scroller.pack(side=TOP, fill=BOTH, expand=True, padx=16, pady=(2, 10))
        if not self.fields:
            self._empty_state(
                scroller,
                "読み取り項目がありません",
                "「範囲・列」で読み取り範囲を作成してください。",
                icon="table",
            )
            return
        if not self.original_image:
            self._empty_state(
                scroller,
                "画像がありません",
                "上部から画像フォルダを選択してください。",
                icon="folder",
            )
            return

        ctk.CTkLabel(
            body,
            text=THEME.copy.recognition_test_note,
            anchor="w",
            justify="left",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
            wraplength=350,
        ).pack(fill="x", padx=18, pady=(0, 8), before=scroller._parent_frame)

        toolbar = ctk.CTkFrame(body, corner_radius=0, fg_color="transparent")
        toolbar.pack(fill="x", padx=16, pady=(0, 10), before=scroller._parent_frame)
        toolbar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            toolbar,
            text=self._text_formatting_summary(),
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        ).grid(row=0, column=0, sticky="ew", padx=(2, 8))
        ctk.CTkButton(
            toolbar,
            text="全項目をテスト",
            image=ICONS.get("scan", 16, THEME.palette.on_color),
            compound="left",
            command=self.ocr_current_image,
            width=138,
            height=THEME.layout.entry_height,
            font=UI_FONT_SMALL,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_PRIMARY_HOVER,
        ).grid(row=0, column=1, sticky="e")

        self._ensure_result_buffers()
        self._ensure_review_selection()
        self.result_vars = []
        for idx, field in enumerate(self.fields):
            if not field.enabled:
                continue
            self._review_result_row(scroller, idx)

        self.review_detail_host = ctk.CTkFrame(
            body, corner_radius=0, fg_color=COLOR_SURFACE
        )
        self.review_detail_host.pack(side=BOTTOM, fill="x")
        self._render_postprocess_panel(self.review_detail_host)

    def _coordinate_status_presentation(self) -> tuple[str, str]:
        if self.coordinate_settings.coordinate_space == COORDINATE_SPACE_CONTENT:
            return (
                ("補正: 黒余白基準", THEME.palette.toolbar_muted)
                if self.current_content_rect
                else (
                    "補正: 画像全体（黒余白未検出）",
                    THEME.palette.toolbar_muted,
                )
            )
        return "補正: 画像全体", THEME.palette.toolbar_muted

    def _update_coordinate_status_display(self) -> None:
        label = getattr(self, "coordinate_status_label", None)
        if label is None:
            return
        visible = (
            self.mode_var.get() == MODE_REVIEW
            and self.original_image is not None
        )
        if not visible:
            self.coordinate_status_var.set("")
            label.grid_remove()
            return
        text, color = self._coordinate_status_presentation()
        self.coordinate_status_var.set(text)
        label.configure(text_color=color)
        label.grid()

    def _review_result_row(self, parent, idx: int) -> None:
        field = self.fields[idx]
        selected = idx == self.selected_index
        row = ctk.CTkFrame(
            parent,
            corner_radius=THEME.layout.panel_radius,
            fg_color=THEME.palette.selected_row if selected else COLOR_SURFACE,
            border_width=1,
            border_color=COLOR_PRIMARY if selected else COLOR_BORDER,
        )
        row.pack(fill="x", padx=8, pady=4)
        self.field_row_by_index[idx] = row
        row.grid_columnconfigure(1, weight=1)

        order_label = ctk.CTkLabel(
            row,
            text=self._field_order_label(idx),
            width=42,
            height=22,
            font=UI_FONT_SMALL,
            fg_color=(
                COLOR_PRIMARY
                if selected
                else THEME.palette.info
                if self._is_review_candidate(field)
                else COLOR_SECONDARY
            ),
            text_color=THEME.palette.on_color,
            corner_radius=4,
        )
        order_label.grid(row=0, column=0, sticky="w", padx=(8, 8), pady=8)
        self.review_order_label_by_index[idx] = order_label
        ctk.CTkLabel(
            row, text=field.name, anchor="w", font=UI_FONT_BOLD, text_color=COLOR_TEXT
        ).grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=8)

        for widget in [row, *row.winfo_children()]:
            widget.bind("<Button-1>", lambda _event, i=idx: self.select_review_field(i))

    def _render_postprocess_panel(self, parent) -> None:
        idx = self.selected_index
        if (
            idx is None
            or not (0 <= idx < len(self.fields))
            or not self.fields[idx].enabled
        ):
            self._empty_state(
                parent,
                "確認対象がありません",
                "有効な項目を選択",
                icon="circle",
            )
            return

        panel = ctk.CTkFrame(
            parent,
            corner_radius=THEME.layout.panel_radius,
            fg_color=COLOR_SURFACE_ALT,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        panel.pack(fill="x", padx=16, pady=(0, 12))
        panel.grid_columnconfigure(1, weight=1)

        self._update_review_detail_values()
        self._result_preview(panel)

        ctk.CTkLabel(
            panel,
            text="OCR方式",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        ).grid(row=1, column=0, sticky="w", padx=(12, 8), pady=2)
        ctk.CTkOptionMenu(
            panel,
            variable=self.review_line_split_var,
            values=OCR_MODE_OPTIONS,
            command=self.set_selected_field_line_split,
            height=THEME.layout.entry_height,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_PRIMARY_HOVER,
            dropdown_fg_color=COLOR_SURFACE_ALT,
            dropdown_hover_color=COLOR_SECONDARY_HOVER,
            text_color=COLOR_TEXT,
        ).grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=2)
        ctk.CTkButton(
            panel,
            text="選択項目をテスト",
            image=ICONS.get("scan", 16, COLOR_TEXT),
            compound="left",
            command=self.ocr_selected_field,
            height=THEME.layout.entry_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            border_width=1,
            border_color=COLOR_BORDER,
        ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(6, 12))
        if self._is_review_candidate(self.fields[idx]):
            ctk.CTkButton(
                panel,
                text="範囲を修正する",
                command=self.edit_selected_field_range,
                height=THEME.layout.entry_height,
                corner_radius=THEME.layout.control_radius,
                font=UI_FONT_SMALL,
                fg_color=COLOR_SECONDARY,
                hover_color=COLOR_SECONDARY_HOVER,
                border_width=1,
                border_color=COLOR_BORDER,
                text_color=COLOR_TEXT,
            ).grid(
                row=3,
                column=0,
                columnspan=2,
                sticky="ew",
                padx=12,
                pady=(0, 12),
            )

    def _result_preview(self, parent) -> None:
        preview = ctk.CTkFrame(
            parent,
            corner_radius=THEME.layout.panel_radius,
            fg_color=COLOR_SURFACE,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        preview.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 6))
        preview.grid_columnconfigure(0, weight=1)
        crop_image = self._selected_field_crop_preview()
        if crop_image is not None:
            ctk.CTkLabel(
                preview,
                text="",
                image=crop_image,
                height=112,
                fg_color=COLOR_CANVAS_BG,
                corner_radius=4,
            ).grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
        else:
            ctk.CTkLabel(
                preview,
                text="範囲プレビューを表示できません",
                height=112,
                font=UI_FONT_SMALL,
                text_color=COLOR_MUTED,
            ).grid(row=0, column=0, sticky="ew", padx=8, pady=(12, 8))

        ctk.CTkLabel(
            preview,
            text="認識結果",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        ).grid(row=1, column=0, sticky="ew", padx=8, pady=(2, 2))
        result_text = (
            self.current_results[self.selected_index]
            if self.selected_index is not None
            and self.selected_index < len(self.current_results)
            else ""
        )
        self._readonly_textbox(preview, result_text or "未テスト", row=2, height=72)

    def _selected_field_crop_preview(self) -> ctk.CTkImage | None:
        if (
            self.original_image is None
            or self.selected_index is None
            or not (0 <= self.selected_index < len(self.fields))
        ):
            self.review_crop_preview = None
            return None
        region = self._scaled_field(self.fields[self.selected_index]).normalized()
        if region.x2 <= region.x1 or region.y2 <= region.y1:
            self.review_crop_preview = None
            return None
        crop = self.original_image.crop((region.x1, region.y1, region.x2, region.y2))
        max_width, max_height = 320, 112
        scale = min(max_width / crop.width, max_height / crop.height, 1.0)
        display_size = (
            max(1, round(crop.width * scale)),
            max(1, round(crop.height * scale)),
        )
        self.review_crop_preview = ctk.CTkImage(
            light_image=crop, dark_image=crop, size=display_size
        )
        return self.review_crop_preview

    def _readonly_textbox(self, parent, value: str, row: int, height: int) -> None:
        textbox = ctk.CTkTextbox(
            parent,
            height=height,
            wrap="word",
            font=UI_FONT_SMALL,
            fg_color=COLOR_INPUT,
            border_width=1,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        )
        textbox.grid(row=row, column=0, sticky="ew", padx=8, pady=(0, 8))
        textbox.insert("1.0", value)
        textbox.configure(state="disabled")

    # Future correction-rule editor scaffold.
    # Intentionally not rendered from _render_review_mode while the UX is being redesigned.
    # Do not remove without also removing CorrectionRule serialization and OCR plumbing.
    def _render_correction_rules_panel(self) -> None:
        host = self.correction_rules_host
        if host is None or not host.winfo_exists():
            return
        for child in host.winfo_children():
            child.destroy()

        panel = ctk.CTkFrame(
            host,
            corner_radius=THEME.layout.panel_radius,
            fg_color=COLOR_SURFACE_ALT,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        panel.pack(fill="x", padx=16, pady=(0, 12))
        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 6))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="補正ルール",
            anchor="w",
            font=UI_FONT_BOLD,
            text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="ew")
        disclosure_button = ctk.CTkButton(
            header,
            text="",
            image=ICONS.get(
                "chevron_up" if self.correction_rules_expanded else "chevron_right",
                15,
                COLOR_MUTED,
            ),
            command=self.toggle_correction_rules_panel,
            width=30,
            height=28,
            corner_radius=THEME.layout.control_radius,
            fg_color="transparent",
            hover_color=COLOR_UTILITY_HOVER,
            border_width=0,
        )
        disclosure_button.grid(row=0, column=1, sticky="e")
        add_tooltip(
            disclosure_button,
            "補正ルールを閉じる"
            if self.correction_rules_expanded
            else "補正ルールを開く",
        )

        self._correction_result_preview(panel)
        if not self.correction_rules_expanded:
            return
        if not self.correction_rules:
            note = ctk.CTkFrame(
                panel, fg_color=COLOR_SURFACE, corner_radius=THEME.layout.panel_radius
            )
            note.pack(fill="x", padx=12, pady=(0, 8))
            ctk.CTkLabel(
                note,
                text="OCRの誤認を置換で補正します。例: ョンで → ヨン",
                anchor="w",
                justify="left",
                font=UI_FONT_SMALL,
                text_color=COLOR_MUTED,
                wraplength=310,
            ).pack(fill="x", padx=10, pady=10)
            self._correction_add_button(panel)
            return

        scroller = ctk.CTkScrollableFrame(
            panel,
            height=190,
            corner_radius=THEME.layout.panel_radius,
            fg_color=COLOR_SURFACE,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        scroller.pack(fill=BOTH, expand=True, padx=12, pady=(0, 8))
        for idx, rule in enumerate(self.correction_rules):
            self._correction_rule_row(scroller, idx, rule)
        self._correction_add_button(panel)

    def _correction_result_preview(self, parent) -> None:
        preview = ctk.CTkFrame(
            parent,
            corner_radius=THEME.layout.panel_radius,
            fg_color=COLOR_SURFACE,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        preview.pack(fill="x", padx=12, pady=(0, 8))
        preview.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            preview,
            text="補正後",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        ).grid(row=0, column=0, sticky="nw", padx=(8, 8), pady=7)
        ctk.CTkLabel(
            preview,
            textvariable=self.review_processed_preview_var,
            anchor="w",
            justify="left",
            font=UI_FONT_SMALL,
            text_color=COLOR_TEXT,
            wraplength=270,
        ).grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=7)

    def toggle_correction_rules_panel(self) -> None:
        self.correction_rules_expanded = not self.correction_rules_expanded
        self._render_correction_rules_panel()

    def _correction_add_button(self, parent) -> None:
        ctk.CTkButton(
            parent,
            text="ルールを追加",
            image=ICONS.get("plus", 16, THEME.palette.on_color),
            compound="left",
            command=self.add_correction_rule,
            height=THEME.layout.entry_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            border_width=1,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        ).pack(fill="x", padx=12, pady=(0, 12))

    def _correction_rule_row(self, parent, idx: int, rule: CorrectionRule) -> None:
        row = ctk.CTkFrame(
            parent,
            corner_radius=THEME.layout.panel_radius,
            fg_color=COLOR_SURFACE_ALT,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        row.pack(fill="x", padx=6, pady=5)
        row.grid_columnconfigure(1, weight=1)
        row.grid_columnconfigure(2, weight=1)

        enabled_var = BooleanVar(value=rule.enabled)
        ctk.CTkCheckBox(
            row,
            text="",
            variable=enabled_var,
            width=22,
            checkbox_width=18,
            checkbox_height=18,
            corner_radius=4,
            border_width=1,
            border_color=COLOR_BORDER,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_PRIMARY_HOVER,
            command=lambda i=idx, v=enabled_var: self.set_correction_rule_enabled(
                i, v.get()
            ),
        ).grid(row=0, column=0, rowspan=2, sticky="nw", padx=(8, 0), pady=9)

        target_var = StringVar(
            value=(
                rule.target
                if rule.target in self._correction_target_options()
                else CORRECTION_ALL_TARGET
            )
        )
        ctk.CTkOptionMenu(
            row,
            variable=target_var,
            values=self._correction_target_options(),
            command=lambda value, i=idx: self.set_correction_rule_target(i, value),
            height=28,
            width=104,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_PRIMARY_HOVER,
            dropdown_fg_color=COLOR_SURFACE_ALT,
            dropdown_hover_color=COLOR_SECONDARY_HOVER,
            text_color=COLOR_TEXT,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 4), pady=(8, 3))
        ctk.CTkButton(
            row,
            text="削除",
            image=ICONS.get("trash", 15, THEME.palette.on_color),
            compound="left",
            command=lambda i=idx: self.delete_correction_rule(i),
            width=72,
            height=28,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_DANGER,
            hover_color=COLOR_DANGER_HOVER,
        ).grid(row=0, column=2, sticky="e", padx=(4, 8), pady=(8, 3))

        pattern_var = StringVar(value=rule.pattern)
        replacement_var = StringVar(value=rule.replacement)
        pattern_var.trace_add(
            "write",
            lambda *_args, i=idx, v=pattern_var: self.set_correction_rule_text(
                i, "pattern", v.get()
            ),
        )
        replacement_var.trace_add(
            "write",
            lambda *_args, i=idx, v=replacement_var: self.set_correction_rule_text(
                i, "replacement", v.get()
            ),
        )
        self.result_vars.extend([target_var, pattern_var, replacement_var])
        ctk.CTkEntry(
            row,
            textvariable=pattern_var,
            placeholder_text="置換前",
            height=THEME.layout.entry_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_INPUT,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        ).grid(row=1, column=1, sticky="ew", padx=(8, 4), pady=(3, 8))
        ctk.CTkEntry(
            row,
            textvariable=replacement_var,
            placeholder_text="置換後",
            height=THEME.layout.entry_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_INPUT,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        ).grid(row=1, column=2, sticky="ew", padx=(4, 8), pady=(3, 8))

    def _correction_target_options(self) -> list[str]:
        options = [CORRECTION_ALL_TARGET]
        for field in self.fields:
            if field.name not in options:
                options.append(field.name)
        return options

    def add_correction_rule(self) -> None:
        target = (
            self.fields[self.selected_index].name
            if self.selected_index is not None
            and 0 <= self.selected_index < len(self.fields)
            else CORRECTION_ALL_TARGET
        )
        self.correction_rules.append(CorrectionRule("", "", target, True))
        self.correction_rules_expanded = True
        self._mark_dirty()
        self.status_var.set("補正ルールを追加しました。")
        self._render_correction_rules_panel()

    def delete_correction_rule(self, idx: int) -> None:
        if not (0 <= idx < len(self.correction_rules)):
            return
        del self.correction_rules[idx]
        self._mark_dirty()
        self._apply_correction_rules_to_current_results()
        self.status_var.set("補正ルールを削除しました。")
        self._render_correction_rules_panel()

    def set_correction_rule_enabled(self, idx: int, enabled: bool) -> None:
        if not (0 <= idx < len(self.correction_rules)):
            return
        self.correction_rules[idx].enabled = enabled
        self._mark_dirty()
        self._on_correction_rules_changed()

    def set_correction_rule_target(self, idx: int, target: str) -> None:
        if not (0 <= idx < len(self.correction_rules)):
            return
        self.correction_rules[idx].target = (
            target
            if target in self._correction_target_options()
            else CORRECTION_ALL_TARGET
        )
        self._mark_dirty()
        self._on_correction_rules_changed()

    def set_correction_rule_text(self, idx: int, attr: str, value: str) -> None:
        if not (0 <= idx < len(self.correction_rules)) or attr not in {
            "pattern",
            "replacement",
        }:
            return
        setattr(self.correction_rules[idx], attr, value)
        self._mark_dirty()
        self._on_correction_rules_changed()

    def _on_correction_rules_changed(self) -> None:
        self._apply_correction_rules_to_current_results()
        self.status_var.set("補正ルールを更新しました。OCR結果に反映されます。")

    def _apply_correction_rules_to_current_results(self) -> None:
        self._ensure_result_buffers()
        for idx, field in enumerate(self.fields):
            raw_text = (
                self.current_raw_results[idx]
                if idx < len(self.current_raw_results)
                else ""
            )
            if raw_text:
                corrected = apply_correction_rules(
                    raw_text, field, self.correction_rules
                )
                self.current_results[idx] = apply_postprocess(
                    corrected,
                    field,
                    preserve_whitespace=field.ocr_line_split == "detected",
                )
            self._refresh_review_row(idx)
        self._update_review_detail_values()

    def _compact_preview_text(self, value: str, limit: int = 34) -> str:
        value = " ".join(value.split())
        return value if len(value) <= limit else f"{value[:limit - 3]}..."

    @staticmethod
    def _compact_path(value: str, limit: int = 34) -> str:
        if len(value) <= limit:
            return value
        head = (limit - 1) // 2
        tail = limit - head - 1
        return f"{value[:head]}…{value[-tail:]}"

    def _text_formatting_summary(self) -> str:
        line_join = {
            LINE_JOIN_NONE: "空白なし",
            LINE_JOIN_FULLWIDTH_SPACE: "全角空白",
            LINE_JOIN_NEWLINE: "改行",
        }[self.text_formatting.line_join]
        formatting = [line_join]
        if self.text_formatting.fullwidth_ascii:
            formatting.append("全角化")
        return f"テキスト整形：{'・'.join(formatting)}"

    def _is_review_candidate(self, field: TemplateField) -> bool:
        return field.slot_key in {
            column.key for column in self.set_definition.extra_slots
        }

    def edit_selected_field_range(self) -> None:
        if self.selected_index is None:
            return
        self.mode_var.set(MODE_TEMPLATE)
        self.status_var.set(
            f"{self.fields[self.selected_index].name} の範囲をプレビュー上で調整できます。"
        )
        self._schedule_side_body_render()
        self.redraw()

    def _has_legacy_postprocess(self, field: TemplateField) -> bool:
        return bool(
            field.replace_from
            or field.remove_text
            or (field.postprocess and field.postprocess != "そのまま")
            or self._has_correction_rule_for_field(field)
        )

    def _has_correction_rule_for_field(self, field: TemplateField) -> bool:
        return any(
            rule.enabled
            and rule.pattern
            and rule.target in {CORRECTION_ALL_TARGET, field.name}
            for rule in self.correction_rules
        )

    def _line_split_display(self, value: str) -> str:
        return "行切り出し" if value == "detected" else "範囲全体"

    def _line_split_value(self, display: str) -> str:
        return "detected" if display == "行切り出し" else "none"

    def select_review_field(self, idx: int) -> str:
        if not (0 <= idx < len(self.fields)):
            return "break"
        self._select_field_index(idx, update_review_detail=True)
        return "break"

    def set_selected_field_line_split(self, value: str) -> None:
        if self.selected_index is None:
            return
        self.set_field_line_split(self.selected_index, value)

    def set_field_line_split(self, idx: int, value: str) -> None:
        if not (0 <= idx < len(self.fields)):
            return
        field = self.fields[idx]
        line_split = self._line_split_value(value)
        if field.ocr_line_split == line_split:
            return
        field.ocr_line_split = line_split
        self._mark_dirty()
        if idx < len(self.current_results):
            self.current_results[idx] = ""
        if idx < len(self.current_raw_results):
            self.current_raw_results[idx] = ""
        self.status_var.set(
            f"{field.name} のOCR方式を {self._line_split_display(field.ocr_line_split)} に変更しました。"
        )
        self._update_review_detail_values()
        self._refresh_review_row(idx)
        self.redraw()

    def _render_export_mode(self) -> None:
        body = self.side_body
        assert body is not None

        content = ctk.CTkScrollableFrame(
            body,
            corner_radius=0,
            fg_color=COLOR_SURFACE,
            scrollbar_button_color=COLOR_SECONDARY,
            scrollbar_button_hover_color=COLOR_SECONDARY_HOVER,
        )
        content.pack(side=TOP, fill=BOTH, expand=True)

        panel = ctk.CTkFrame(
            content,
            corner_radius=THEME.layout.panel_radius,
            fg_color=COLOR_SURFACE_ALT,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        panel.pack(fill="x", padx=16, pady=(0, 10))
        panel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            panel,
            text="画像フォルダ",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="ew", padx=(12, 8), pady=(12, 2))
        ctk.CTkLabel(
            panel,
            textvariable=self.folder_var,
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
            wraplength=210,
        ).grid(row=1, column=0, sticky="ew", padx=(12, 8), pady=(0, 8))
        ctk.CTkButton(
            panel,
            text="変更",
            image=ICONS.get("folder", 16, COLOR_TEXT),
            compound="left",
            command=self.open_image_folder,
            width=104,
            height=THEME.layout.entry_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            border_width=1,
            border_color=COLOR_BORDER,
        ).grid(row=0, column=1, rowspan=2, sticky="e", padx=(0, 12), pady=(12, 8))
        ctk.CTkLabel(
            panel,
            text="出力ファイル",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_TEXT,
        ).grid(row=2, column=0, sticky="ew", padx=(12, 8), pady=(2, 2))
        ctk.CTkLabel(
            panel,
            textvariable=self.output_var,
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
            wraplength=210,
        ).grid(row=3, column=0, sticky="ew", padx=(12, 8), pady=(0, 12))
        ctk.CTkButton(
            panel,
            text="選択",
            image=ICONS.get("table", 16, COLOR_TEXT),
            compound="left",
            command=self.select_output_file,
            width=104,
            height=THEME.layout.entry_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            border_width=1,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        ).grid(row=2, column=1, rowspan=2, sticky="e", padx=(0, 12), pady=(2, 12))

        self._render_image_queue(content)
        self._render_excel_output_settings(content)

        enabled_count = len(self._enabled_fields())
        image_count = len(self.image_queue.included_files())
        self._render_export_structure_summary(content, image_count, enabled_count)

        actions = ctk.CTkFrame(body, fg_color=COLOR_SURFACE)
        actions.pack(side=BOTTOM, fill="x", padx=16, pady=(8, 14))
        actions.grid_columnconfigure(0, weight=1)
        retry_count = (
            len(self.image_queue.failed_files()) if self.last_export_result else 0
        )
        self.retry_button = ctk.CTkButton(
            actions,
            text=f"失敗分を再実行（{retry_count}）",
            image=ICONS.get("refresh", 17, COLOR_TEXT),
            compound="left",
            command=self.retry_failed_images,
            state="normal" if retry_count else "disabled",
            height=THEME.layout.primary_button_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        self.retry_button.grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(0, 6),
        )
        ctk.CTkButton(
            actions,
            text="Excel出力",
            image=ICONS.get("table", 18, THEME.palette.on_color),
            compound="left",
            command=self.export_to_excel,
            height=THEME.layout.export_button_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_BOLD,
            fg_color=COLOR_CTA,
            hover_color=COLOR_CTA_HOVER,
        ).grid(row=1, column=0, sticky="ew", padx=(0, 4))
        self.export_more_button = ctk.CTkButton(
            actions,
            text="",
            image=ICONS.get("more", 18, COLOR_MUTED),
            command=lambda: None,
            width=40,
            height=THEME.layout.export_button_height,
            corner_radius=THEME.layout.control_radius,
            fg_color="transparent",
            hover_color=COLOR_UTILITY_HOVER,
            border_width=0,
        )
        self.export_more_button.configure(
            command=lambda button=self.export_more_button: (
                self.show_export_actions_menu(button)
            )
        )
        self.export_more_button.grid(row=1, column=1)
        add_tooltip(
            self.export_more_button,
            "その他の出力",
        )
        ctk.CTkLabel(
            actions,
            text="Excelがない場合は、右のメニューからCSVで保存できます。",
            anchor="e",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        ).grid(row=2, column=0, columnspan=2, sticky="e", pady=(3, 0))

    def _render_image_queue(self, parent) -> None:
        panel = ctk.CTkFrame(
            parent,
            corner_radius=THEME.layout.panel_radius,
            fg_color=COLOR_SURFACE_ALT,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        panel.pack(fill="x", padx=16, pady=(0, 10))
        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(8, 6))
        header.grid_columnconfigure(0, weight=1)
        self._update_queue_summary()
        ctk.CTkLabel(
            header,
            textvariable=self.queue_summary_var,
            anchor="w",
            font=UI_FONT_BOLD,
            text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            header,
            text="全選択",
            command=lambda: self.set_all_queue_items(True),
            width=58,
            height=26,
            font=UI_FONT_SMALL,
            fg_color="transparent",
            hover_color=COLOR_UTILITY_HOVER,
            border_width=0,
            text_color=COLOR_MUTED,
        ).grid(row=0, column=1, padx=(4, 2))
        ctk.CTkButton(
            header,
            text="全解除",
            command=lambda: self.set_all_queue_items(False),
            width=58,
            height=26,
            font=UI_FONT_SMALL,
            fg_color="transparent",
            hover_color=COLOR_UTILITY_HOVER,
            border_width=0,
            text_color=COLOR_MUTED,
        ).grid(row=0, column=2, padx=(2, 0))

        queue_frame = ctk.CTkScrollableFrame(
            panel,
            height=190,
            corner_radius=4,
            fg_color=COLOR_SURFACE,
            scrollbar_button_color=COLOR_SECONDARY,
            scrollbar_button_hover_color=COLOR_SECONDARY_HOVER,
        )
        queue_frame.pack(fill="x", padx=8, pady=(0, 8))
        self.queue_include_vars = {}
        self.queue_status_labels = {}
        if not self.image_queue.items:
            ctk.CTkLabel(
                queue_frame,
                text="画像フォルダを選択すると、自然順で処理対象を表示します。",
                anchor="w",
                justify="left",
                wraplength=300,
                font=UI_FONT_SMALL,
                text_color=COLOR_MUTED,
            ).pack(fill="x", padx=8, pady=16)
            return
        for index, item in enumerate(self.image_queue.items, start=1):
            self._queue_row(queue_frame, index, item)

    def _queue_row(self, parent, index: int, item: ImageQueueItem) -> None:
        row = ctk.CTkFrame(parent, corner_radius=0, fg_color="transparent", height=30)
        row.pack(fill="x", padx=2, pady=1)
        row.grid_columnconfigure(2, weight=1)
        include_var = BooleanVar(value=item.included)
        self.queue_include_vars[item.path] = include_var
        ctk.CTkCheckBox(
            row,
            text="",
            variable=include_var,
            width=24,
            checkbox_width=18,
            checkbox_height=18,
            corner_radius=4,
            border_width=1,
            command=lambda path=item.path, variable=include_var: self.set_queue_item_included(
                path, variable.get()
            ),
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_PRIMARY_HOVER,
        ).grid(row=0, column=0, padx=(4, 2), pady=3)
        ctk.CTkLabel(
            row,
            text=f"{index:03}",
            width=34,
            anchor="e",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        ).grid(row=0, column=1, padx=(0, 6))
        filename = ctk.CTkLabel(
            row,
            text=self._compact_queue_filename(item.path.name),
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_TEXT if item.included else COLOR_MUTED,
            cursor="hand2",
        )
        filename.grid(row=0, column=2, sticky="ew")
        filename.bind(
            "<Button-1>", lambda _event, path=item.path: self.show_queue_image(path)
        )
        status_text, status_color, status_icon = self._queue_status_presentation(item)
        status = ctk.CTkLabel(
            row,
            text=status_text,
            image=ICONS.get(status_icon, 15, status_color),
            compound="left",
            width=82,
            anchor="e",
            font=UI_FONT_SMALL,
            text_color=status_color,
        )
        status.grid(row=0, column=3, padx=(6, 4))
        self.queue_status_labels[item.path] = status

    def _queue_status_presentation(self, item: ImageQueueItem) -> tuple[str, str, str]:
        presentations = {
            STATUS_PENDING: ("待機", COLOR_MUTED, "circle"),
            STATUS_PROCESSING: ("処理中", THEME.palette.info, "refresh"),
            STATUS_SUCCESS: ("完了", THEME.palette.success, "check_circle"),
            STATUS_FAILED: ("要再実行", COLOR_DANGER, "alert_circle"),
            STATUS_EXCLUDED: ("除外", THEME.palette.disabled_fill, "minus_circle"),
        }
        return presentations.get(item.status, ("待機", COLOR_MUTED, "circle"))

    def _compact_queue_filename(self, value: str, limit: int = 23) -> str:
        if len(value) <= limit:
            return value
        path = Path(value)
        suffix = path.suffix
        stem_limit = max(6, limit - len(suffix) - 3)
        return f"{path.stem[:stem_limit]}...{suffix}"

    def _update_queue_summary(self) -> None:
        included = len(self.image_queue.included_files())
        total = len(self.image_queue.items)
        self.queue_summary_var.set(f"処理対象 {included} / {total}")
        if self.retry_button is not None and self.retry_button.winfo_exists():
            retry_count = (
                len(self.image_queue.failed_files()) if self.last_export_result else 0
            )
            self.retry_button.configure(
                text=f"失敗分を再実行（{retry_count}）",
                state="normal" if retry_count else "disabled",
            )

    def _refresh_queue_item_status(self, path: Path) -> None:
        item = next(
            (
                candidate
                for candidate in self.image_queue.items
                if candidate.path == path
            ),
            None,
        )
        label = self.queue_status_labels.get(path)
        if item is None or label is None or not label.winfo_exists():
            return
        status_text, color, status_icon = self._queue_status_presentation(item)
        label.configure(
            text=status_text, text_color=color, image=ICONS.get(status_icon, 15, color)
        )

    def set_queue_item_included(self, path: Path, included: bool) -> None:
        self.image_queue.set_included(path, included)
        self._refresh_queue_item_status(path)
        self._update_queue_summary()

    def set_all_queue_items(self, included: bool) -> None:
        self.image_queue.set_all(included)
        for path, variable in self.queue_include_vars.items():
            variable.set(included)
            self._refresh_queue_item_status(path)
        self._update_queue_summary()

    def show_queue_image(self, path: Path) -> None:
        try:
            self.current_image_index = self.image_files.index(path)
        except ValueError:
            return
        self._load_current_image(auto_ocr=False)
        item = next(
            (
                candidate
                for candidate in self.image_queue.items
                if candidate.path == path
            ),
            None,
        )
        if item is not None and item.detail:
            self.status_var.set(f"{path.name}: {item.detail}")

    def _clear_retry_context(self, reset_queue: bool = False) -> None:
        self.last_export_result = None
        self.last_export_signature = None
        self.last_export_output_path = None
        self.last_export_format = ""
        if reset_queue:
            self.image_queue.prepare(self.image_queue.included_files())
        self._update_queue_summary()

    def _export_structure_signature(
        self, fields: list[TemplateField], settings: ExportSettings
    ) -> tuple:
        return (
            settings.sheet_name,
            settings.start_row,
            settings.start_col,
            settings.include_filename,
            settings.include_header,
            settings.output_layout,
            self.set_definition.preset,
            self.set_definition.order_label,
            tuple(
                (column.key, column.label, column.ocr_line_split)
                for column in self.set_definition.columns
            ),
            tuple((field.name, field.set_id, field.slot_key) for field in fields),
        )

    def _render_excel_output_settings(self, parent) -> None:
        panel = ctk.CTkFrame(
            parent,
            corner_radius=THEME.layout.panel_radius,
            fg_color=COLOR_SURFACE_ALT,
            border_width=1,
            border_color=COLOR_BORDER,
        )
        panel.pack(fill="x", padx=16, pady=(0, 10))
        panel.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            panel,
            text="シート名",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        ).grid(row=0, column=0, sticky="w", padx=(12, 8), pady=(12, 4))
        ctk.CTkEntry(
            panel,
            textvariable=self.export_sheet_var,
            height=THEME.layout.entry_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_INPUT,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        ).grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(12, 4))

        ctk.CTkLabel(
            panel,
            text="書き込み",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        ).grid(row=1, column=0, sticky="w", padx=(12, 8), pady=4)
        ctk.CTkOptionMenu(
            panel,
            variable=self.export_write_mode_var,
            values=["上書き", "追記"],
            height=THEME.layout.entry_height,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_PRIMARY_HOVER,
            dropdown_fg_color=COLOR_SURFACE_ALT,
            dropdown_hover_color=COLOR_SECONDARY_HOVER,
            text_color=COLOR_TEXT,
        ).grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=4)

        ctk.CTkLabel(
            panel,
            text="開始セル",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        ).grid(row=2, column=0, sticky="w", padx=(12, 8), pady=4)
        ctk.CTkEntry(
            panel,
            textvariable=self.export_start_cell_var,
            height=THEME.layout.entry_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_INPUT,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        ).grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=4)

        ctk.CTkCheckBox(
            panel,
            text="ファイル名列",
            variable=self.export_include_filename_var,
            command=self._render_side_body,
            checkbox_width=18,
            checkbox_height=18,
            corner_radius=4,
            border_width=1,
            font=UI_FONT_SMALL,
            text_color=COLOR_TEXT,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_PRIMARY_HOVER,
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 4))
        ctk.CTkCheckBox(
            panel,
            text="ヘッダー行",
            variable=self.export_include_header_var,
            command=self._render_side_body,
            checkbox_width=18,
            checkbox_height=18,
            corner_radius=4,
            border_width=1,
            font=UI_FONT_SMALL,
            text_color=COLOR_TEXT,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_PRIMARY_HOVER,
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=12, pady=(2, 12))

    def _render_export_structure_summary(
        self, parent, image_count: int, enabled_count: int
    ) -> None:
        summary = ctk.CTkFrame(parent, fg_color="transparent")
        summary.pack(fill="x", padx=18, pady=(0, 12))
        summary.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            summary,
            text=(
                f"{image_count}画像（自然順） / "
                f"{self._export_unit_summary(enabled_count)} / "
                f"{self._export_column_summary()}"
            ),
            anchor="w",
            justify="left",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
            wraplength=330,
        ).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(
            summary,
            text="Excelの行単位",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        ).grid(row=1, column=0, sticky="w", padx=(0, 8))
        ctk.CTkLabel(
            summary,
            text=EXPORT_LAYOUT_DISPLAY[self.export_layout_var.get()],
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_TEXT,
        ).grid(row=1, column=1, columnspan=2, sticky="ew")
        edit_button_row = (
            3
            if self.export_layout_var.get() == EXPORT_LAYOUT_SET
            else 2
        )
        ctk.CTkButton(
            summary,
            text="範囲・列で編集",
            image=ICONS.get("chevron_left", 14, COLOR_TEXT),
            compound="left",
            command=self.open_template_mode_from_export,
            width=124,
            height=THEME.layout.entry_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            border_width=1,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        ).grid(
            row=edit_button_row,
            column=0,
            columnspan=3,
            sticky="e",
            pady=(8, 0),
        )
        if self.export_layout_var.get() == EXPORT_LAYOUT_SET:
            ctk.CTkLabel(
                summary,
                text="項目の組み合わせ",
                anchor="w",
                font=UI_FONT_SMALL,
                text_color=COLOR_MUTED,
            ).grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(6, 0))
            ctk.CTkLabel(
                summary,
                text=self.set_definition.name,
                anchor="w",
                font=UI_FONT_SMALL,
                text_color=COLOR_TEXT,
            ).grid(
                row=2,
                column=1,
                columnspan=2,
                sticky="ew",
                pady=(6, 0),
            )

    def open_template_mode_from_export(self) -> None:
        self.mode_var.set(MODE_TEMPLATE)
        self.status_var.set("Excelの行単位を変更できます。")
        self._on_mode_change()

    def _export_column_summary(self) -> str:
        if self.export_layout_var.get() == EXPORT_LAYOUT_SET:
            names = [
                self.set_definition.order_label,
                *(column.label for column in self.set_definition.columns),
            ]
            if self.export_include_filename_var.get():
                names.insert(0, "画像名")
            columns = "+".join(names)
        else:
            columns = (
                "ファイル名列あり"
                if self.export_include_filename_var.get()
                else "項目のみ"
            )
        header = (
            "ヘッダーあり" if self.export_include_header_var.get() else "ヘッダーなし"
        )
        return f"{columns} / {header}"

    def _export_unit_summary(self, enabled_count: int) -> str:
        if self.export_layout_var.get() == EXPORT_LAYOUT_SET:
            return f"{len(self._set_field_indexes())}セット"
        return f"{enabled_count}項目"

    def _empty_state(
        self,
        parent,
        title: str,
        note: str,
        hint: str = "",
        *,
        icon: str,
    ) -> None:
        box = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        box.pack(fill=BOTH, expand=True, padx=12, pady=20)
        ctk.CTkLabel(
            box,
            text=title,
            image=ICONS.get(icon, 20, COLOR_MUTED),
            compound="top",
            font=UI_FONT_BOLD,
            text_color=COLOR_TEXT,
        ).pack(pady=(28, 6))
        note_label = ctk.CTkLabel(
            box,
            text=note,
            justify="center",
            wraplength=290,
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        )
        note_label.pack(pady=(0, 8 if hint else 28))
        if hint:
            ctk.CTkLabel(
                box,
                text=hint,
                image=ICONS.get("file_input", 14, COLOR_MUTED),
                compound="left",
                justify="center",
                wraplength=290,
                font=UI_FONT_SMALL,
                text_color=COLOR_MUTED,
            ).pack(pady=(0, 28))

    def _field_row(self, parent, idx: int) -> None:
        field = self.fields[idx]
        selected = idx == self.selected_index
        row = ctk.CTkFrame(
            parent,
            corner_radius=THEME.layout.panel_radius,
            fg_color=THEME.palette.selected_row if selected else COLOR_SURFACE,
            border_width=1,
            border_color=COLOR_PRIMARY if selected else COLOR_BORDER,
        )
        row.pack(fill="x", padx=8, pady=5)
        self.field_row_widgets.append(row)
        self.field_row_by_index[idx] = row
        row.grid_columnconfigure(2, weight=1)
        var = BooleanVar(value=field.enabled)
        self.field_check_vars.append(var)
        ctk.CTkCheckBox(
            row,
            text="",
            variable=var,
            width=24,
            checkbox_width=18,
            checkbox_height=18,
            corner_radius=4,
            border_width=1,
            border_color=COLOR_BORDER,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_PRIMARY_HOVER,
            command=lambda i=idx, v=var: self.set_field_enabled(i, v.get()),
        ).grid(row=0, column=0, rowspan=2, padx=(8, 0), pady=8)
        ctk.CTkLabel(
            row,
            text=self._field_order_label(idx),
            width=42,
            height=22,
            font=UI_FONT_SMALL,
            fg_color=COLOR_PRIMARY if field.enabled else THEME.palette.disabled_fill,
            text_color=THEME.palette.on_color,
            corner_radius=4,
        ).grid(row=0, column=1, sticky="w", padx=(4, 6), pady=(8, 2))
        name_widget = self._field_name_widget(row, idx)
        editing = self.editing_name_index == idx
        name_widget.grid(
            row=0,
            column=2,
            columnspan=4 if editing else 1,
            sticky="ew",
            padx=(0, 8 if editing else 6),
            pady=(8, 2),
        )
        ocr_mode_menu = ctk.CTkOptionMenu(
            row,
            values=OCR_MODE_OPTIONS,
            command=lambda value, i=idx: self.set_field_line_split(i, value),
            width=100,
            height=26,
            dynamic_resizing=False,
            font=UI_FONT_SMALL,
            fg_color=(
                THEME.palette.line_badge
                if field.ocr_line_split == "detected" and field.enabled
                else COLOR_SECONDARY
            ),
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_PRIMARY_HOVER,
            dropdown_fg_color=COLOR_SURFACE_ALT,
            dropdown_hover_color=COLOR_SECONDARY_HOVER,
            text_color=COLOR_TEXT,
        )
        ocr_mode_menu.set(self._line_split_display(field.ocr_line_split))
        ocr_mode_menu.grid(row=1, column=4, sticky="e", padx=(4, 8), pady=(0, 8))
        detail_label = ctk.CTkLabel(
            row,
            text=self._field_size_text(field),
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        )
        detail_label.grid(
            row=1, column=1, columnspan=3, sticky="ew", padx=(4, 4), pady=(0, 8)
        )
        edit_button = more_button = drag_handle = None
        if not editing:
            edit_button = self._field_action_button(
                row,
                "pencil",
                lambda i=idx: self.begin_inline_name_edit(i),
                "項目名を編集",
            )
            edit_button.grid(row=0, column=3, sticky="e", padx=(0, 2), pady=(7, 1))
            more_button = self._field_action_button(
                row, "more", lambda: None, "項目の操作"
            )
            more_button.configure(
                command=lambda button=more_button, i=idx: self.show_field_actions_menu(
                    button, i
                )
            )
            more_button.grid(row=0, column=4, sticky="e", padx=2, pady=(7, 1))
            drag_handle = ctk.CTkLabel(
                row,
                text="",
                image=ICONS.get("grip", 16, COLOR_MUTED),
                width=30,
                cursor="fleur",
            )
            drag_handle.grid(
                row=0, column=5, rowspan=2, sticky="e", padx=(0, 6), pady=8
            )
            add_tooltip(drag_handle, "ドラッグして並べ替え")
            self.field_drag_handle_by_index[idx] = drag_handle
            drag_handle.bind(
                "<ButtonPress-1>", lambda event, i=idx: self.start_field_drag(event, i)
            )
            drag_handle.bind(
                "<B1-Motion>", lambda event, i=idx: self.update_field_drag(event, i)
            )
            drag_handle.bind(
                "<ButtonRelease-1>",
                lambda event, i=idx: self.finish_field_drag(event, i),
            )
            drag_handle.bind(
                "<Button-3>",
                lambda event, i=idx: self.show_field_context_menu(event, i),
            )
        excluded = {
            widget
            for widget in (
                name_widget,
                ocr_mode_menu,
                edit_button,
                more_button,
                drag_handle,
            )
            if widget is not None
        }
        interactive_children = [
            child for child in row.winfo_children() if child not in excluded
        ]
        for widget in interactive_children + [row]:
            widget.bind("<Button-1>", lambda _event, i=idx: self.select_field(i))
            widget.bind(
                "<Button-3>",
                lambda event, i=idx: self.show_field_context_menu(event, i),
            )
        name_widget.bind(
            "<Button-3>", lambda event, i=idx: self.show_field_context_menu(event, i)
        )
        if self.editing_name_index != idx:
            name_widget.bind("<Button-1>", lambda _event, i=idx: self.select_field(i))
            name_widget.bind(
                "<Double-Button-1>",
                lambda _event, i=idx: self.begin_inline_name_edit(i),
            )

    def _set_field_row(self, parent, idx: int) -> None:
        field = self.fields[idx]
        selected = idx == self.selected_index
        row = ctk.CTkFrame(
            parent,
            corner_radius=THEME.layout.panel_radius,
            fg_color=THEME.palette.selected_row if selected else COLOR_SURFACE,
            border_width=1,
            border_color=COLOR_PRIMARY if selected else COLOR_BORDER,
        )
        row.pack(fill="x", padx=8, pady=4)
        self.field_row_widgets.append(row)
        self.field_row_by_index[idx] = row
        row.grid_columnconfigure(2, weight=1)
        var = BooleanVar(value=field.enabled)
        self.field_check_vars.append(var)
        ctk.CTkCheckBox(
            row,
            text="",
            variable=var,
            width=24,
            checkbox_width=18,
            checkbox_height=18,
            corner_radius=4,
            border_width=1,
            border_color=COLOR_BORDER,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_PRIMARY_HOVER,
            command=lambda i=idx, v=var: self.set_field_enabled(i, v.get()),
        ).grid(row=0, column=0, rowspan=2, padx=(8, 0), pady=8)
        role_label = self._slot_display(field.slot_key)
        ctk.CTkLabel(
            row,
            text=role_label,
            width=84,
            height=22,
            font=UI_FONT_SMALL,
            fg_color=(
                THEME.palette.info
                if field.enabled
                and field.slot_key
                in {column.key for column in self.set_definition.extra_slots}
                else COLOR_PRIMARY
                if field.enabled and field.slot_key
                else THEME.palette.disabled_fill
            ),
            text_color=THEME.palette.on_color,
            corner_radius=4,
        ).grid(row=0, column=1, sticky="w", padx=(4, 6), pady=(8, 2))
        name_widget = self._field_name_widget(row, idx)
        editing = self.editing_name_index == idx
        name_widget.grid(
            row=0,
            column=2,
            columnspan=5 if editing else 2,
            sticky="ew",
            padx=(0, 8 if editing else 6),
            pady=(8, 2),
        )
        if not editing:
            edit_button = self._field_action_button(
                row,
                "pencil",
                lambda i=idx: self.begin_inline_name_edit(i),
                "項目名を編集",
            )
            edit_button.grid(row=0, column=4, sticky="e", padx=(0, 2), pady=(7, 1))
            more_button = self._field_action_button(
                row, "more", lambda: None, "項目の操作"
            )
            more_button.configure(
                command=lambda button=more_button, i=idx: self.show_field_actions_menu(
                    button, i
                )
            )
            more_button.grid(row=0, column=5, sticky="e", padx=(0, 6), pady=(7, 1))

        role_menu = ctk.CTkOptionMenu(
            row,
            values=[
                "未割当",
                *(column.label for column in self.set_definition.columns),
                *(column.label for column in self.set_definition.extra_slots),
            ],
            command=lambda value, i=idx: self.set_field_slot(i, value),
            width=116,
            height=26,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_PRIMARY_HOVER,
            dropdown_fg_color=COLOR_SURFACE_ALT,
            dropdown_hover_color=COLOR_SECONDARY_HOVER,
            text_color=COLOR_TEXT,
        )
        role_menu.set(role_label)
        role_menu.grid(row=1, column=1, sticky="w", padx=(4, 4), pady=(2, 8))
        ctk.CTkLabel(
            row,
            text=self._field_compact_size_text(field),
            anchor="e",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        ).grid(row=1, column=2, columnspan=4, sticky="e", padx=(4, 4), pady=(2, 8))

        drag_handle = None
        if not editing:
            drag_handle = ctk.CTkLabel(
                row,
                text="",
                image=ICONS.get("grip", 16, COLOR_MUTED),
                width=30,
                cursor="fleur",
            )
            drag_handle.grid(row=0, column=6, rowspan=2, sticky="e", padx=(0, 6), pady=8)
            add_tooltip(drag_handle, "ドラッグして並べ替え・別のセットへ移動")
            self.field_drag_handle_by_index[idx] = drag_handle
            drag_handle.bind(
                "<ButtonPress-1>", lambda event, i=idx: self.start_field_drag(event, i)
            )
            drag_handle.bind(
                "<B1-Motion>", lambda event, i=idx: self.update_field_drag(event, i)
            )
            drag_handle.bind(
                "<ButtonRelease-1>", lambda event, i=idx: self.finish_field_drag(event, i)
            )

        for widget in [row, name_widget]:
            widget.bind("<Button-1>", lambda _event, i=idx: self.select_field(i))
            widget.bind(
                "<Button-3>",
                lambda event, i=idx: self.show_field_context_menu(event, i),
            )
        if self.editing_name_index != idx:
            name_widget.bind(
                "<Double-Button-1>",
                lambda _event, i=idx: self.begin_inline_name_edit(i),
            )

    def _field_action_button(
        self, parent, icon: str, command, tooltip: str
    ) -> ctk.CTkButton:
        button = ctk.CTkButton(
            parent,
            text="",
            image=ICONS.get(icon, 15, COLOR_MUTED),
            command=command,
            width=28,
            height=28,
            corner_radius=THEME.layout.control_radius,
            fg_color="transparent",
            hover_color=COLOR_UTILITY_HOVER,
            border_width=0,
        )
        add_tooltip(button, tooltip)
        return button

    def _slot_display(self, role: str) -> str:
        return (
            self.set_definition.slot_label(role)
            if role in self.set_definition.allowed_slot_keys()
            else "未割当"
        )

    def set_field_slot(self, idx: int, display: str) -> None:
        if not (0 <= idx < len(self.fields)):
            return
        definition = getattr(
            self,
            "set_definition",
            self.profile.default_set_definition,
        )
        role = next(
            (
                column.key
                for column in (*definition.columns, *definition.extra_slots)
                if column.label == display
            ),
            "",
        )
        field = self.fields[idx]
        role_changed = field.slot_key != role
        column = definition.column_for(role)
        desired_line_split = (
            column.ocr_line_split if column is not None else field.ocr_line_split
        )
        line_split_changed = (
            bool(role) and field.ocr_line_split != desired_line_split
        )
        if not role_changed and not line_split_changed:
            return
        field.slot_key = role
        if line_split_changed:
            field.ocr_line_split = desired_line_split
            if idx < len(self.current_results):
                self.current_results[idx] = ""
            if idx < len(self.current_raw_results):
                self.current_raw_results[idx] = ""
        self._mark_dirty()
        if line_split_changed:
            self.status_var.set(
                f"{field.name} を{display}に設定し、OCR方式を{self._line_split_display(desired_line_split)}に変更しました。"
            )
        else:
            self.status_var.set(f"{field.name} のセット内項目を更新しました。")
        self._render_side_body()
        self.redraw()

    def _field_name_widget(self, parent, idx: int):
        field = self.fields[idx]
        if self.editing_name_index != idx:
            return ctk.CTkLabel(
                parent,
                text=field.name,
                anchor="w",
                font=UI_FONT_BOLD,
                text_color=COLOR_TEXT if field.enabled else COLOR_MUTED,
            )

        editor = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        editor.grid_columnconfigure(0, weight=1)
        name_var = StringVar(value=self.editing_name_value or field.name)
        entry = ctk.CTkEntry(
            editor,
            textvariable=name_var,
            height=THEME.layout.entry_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_BOLD,
            fg_color=COLOR_INPUT,
            border_color=COLOR_DANGER if self.editing_name_error else COLOR_PRIMARY,
            text_color=COLOR_TEXT,
        )
        entry.grid(row=0, column=0, sticky="ew")
        entry.bind(
            "<Return>",
            lambda _event, i=idx, v=name_var: self.commit_inline_name_edit(i, v.get()),
        )
        entry.bind("<Escape>", lambda _event: self.cancel_inline_name_edit())
        entry.bind(
            "<FocusOut>",
            lambda _event, i=idx, v=name_var: self.commit_inline_name_edit(i, v.get()),
        )
        error_label = ctk.CTkLabel(
            editor,
            text=self.editing_name_error,
            anchor="w",
            justify="left",
            wraplength=230,
            font=UI_FONT_SMALL,
            text_color=COLOR_DANGER,
        )
        error_label.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        if not self.editing_name_error:
            error_label.grid_remove()
        self.editing_name_entry = entry
        self.editing_name_error_label = error_label
        entry.after(
            10, lambda widget=entry: (widget.focus_set(), widget.select_range(0, "end"))
        )
        return editor

    def start_auto_detection_workflow(self) -> None:
        self.workflow_started = True
        self.auto_detection_enabled = True
        self.empty_set_ids = set()
        self.set_definition = self.profile.default_set_definition
        if hasattr(self, "set_preset_var"):
            self.set_preset_var.set(self.set_definition.name)
        self.auto_detection_base_fields = self.profile.auto_detection_fields()
        self.fields = [replace(field) for field in self.auto_detection_base_fields]
        self.selected_index = 0
        with self._without_dirty_tracking():
            self.export_layout_var.set(EXPORT_LAYOUT_SET)
            self.mode_var.set(MODE_REVIEW)
        self._hide_workflow_choice()
        if not self.open_image_folder():
            self.workflow_started = False
            self.auto_detection_enabled = False
            self.auto_detection_base_fields = []
            self.empty_set_ids = set()
            self.fields = []
            self.selected_index = None
            with self._without_dirty_tracking():
                self.export_layout_var.set(EXPORT_LAYOUT_IMAGE_ROW)
                self.mode_var.set(MODE_TEMPLATE)
            self._show_workflow_choice()
            self._render_side_body()
            return
        self._mark_clean()

    def start_new_template_workflow(self) -> None:
        self.workflow_started = True
        self.auto_detection_enabled = False
        self.auto_detection_base_fields = []
        self.empty_set_ids = set()
        self.set_definition = self.profile.default_set_definition
        if hasattr(self, "set_preset_var"):
            self.set_preset_var.set(self.set_definition.name)
        self.fields = []
        self.selected_index = None
        with self._without_dirty_tracking():
            self.export_layout_var.set(EXPORT_LAYOUT_IMAGE_ROW)
            self.mode_var.set(MODE_TEMPLATE)
        self._hide_workflow_choice()
        if not self.open_image_folder():
            self.workflow_started = False
            self._show_workflow_choice()
            return
        self._mark_clean()

    def continue_without_workflow_choice(self) -> None:
        """Dismiss onboarding without selecting a workflow or image folder."""
        self.workflow_started = True
        self.auto_detection_enabled = False
        self._hide_workflow_choice()
        self.status_var.set(
            "画像フォルダは選択されていません。"
            "上部の「画像フォルダを選択」から開始できます。"
        )

    def _show_workflow_choice(self) -> None:
        ocr_setup_modal = getattr(self, "ocr_setup_modal", None)
        if ocr_setup_modal is not None and ocr_setup_modal.visible:
            return
        if self.workflow_choice_modal is not None:
            self.workflow_choice_modal.show(
                focus=self.workflow_choice_primary_button
            )

    def _hide_workflow_choice(self) -> None:
        if self.workflow_choice_modal is not None:
            self.workflow_choice_modal.hide()

    def show_ocr_setup(self, pending_action=None) -> None:
        if pending_action is not None:
            self._pending_ocr_action = pending_action
        self._refresh_ocr_setup_overlay()
        self._hide_workflow_choice()
        if self.ocr_setup_modal is not None:
            self.ocr_setup_modal.show(focus=self.ocr_setup_primary_button)

    def _refresh_ocr_readiness_banner(
        self, status: OcrEnvironmentStatus | None = None
    ) -> None:
        if (
            self.ocr_readiness_banner is None
            or self.ocr_readiness_label is None
            or self.ocr_readiness_button is None
        ):
            return
        status = status or self.ocr_environment.quick_status()
        if status.ready and not self.ocr_environment_restart_required:
            self.ocr_readiness_banner.pack_forget()
            return
        if self.ocr_environment_restart_required:
            text = "OCR再起動待ち — 範囲編集のみ利用できます"
            color = THEME.palette.warning
            action_text = "再起動"
            action_command = self.restart_for_ocr_cache_change
        elif status.state == OCR_ENV_UNAVAILABLE:
            text = "OCR実行環境が不足 — 範囲編集のみ利用できます"
            color = THEME.palette.danger
            action_text = "修復"
            action_command = self.repair_ocr_runtime
        elif status.state == OCR_ENV_LOCATION_ERROR:
            text = "OCR保存先に問題 — 範囲編集のみ利用できます"
            color = THEME.palette.danger
            action_text = "保存先"
            action_command = self.open_ocr_environment_settings
        elif status.state == OCR_ENV_VERIFY:
            text = "OCRの確認が必要 — 範囲編集のみ利用できます"
            color = THEME.palette.warning
            action_text = "確認"
            action_command = self.show_ocr_setup
        else:
            text = "OCR未準備 — 範囲編集のみ利用できます"
            color = THEME.palette.warning
            action_text = "準備"
            action_command = self.show_ocr_setup
        self.ocr_readiness_banner.configure(border_color=color)
        self.ocr_readiness_label.configure(
            text=text,
            image=ICONS.get("alert_circle", 15, color),
            text_color=color,
        )
        self.ocr_readiness_button.configure(
            text=action_text,
            command=action_command,
        )
        if not self.ocr_readiness_banner.winfo_manager():
            self.ocr_readiness_banner.pack(
                side=TOP,
                fill="x",
                padx=16,
                pady=(0, 10),
                before=self.side_body,
            )

    def _refresh_ocr_setup_overlay(self) -> None:
        if (
            self.ocr_setup_status_label is None
            or self.ocr_setup_note_label is None
            or self.ocr_setup_primary_button is None
        ):
            return
        status = self.ocr_environment.quick_status()
        title = "OCR認識モデルをダウンロード"
        subtitle = (
            "PaddleOCRでの文字認識には、実行環境と認識モデルが必要です。\n"
            "この端末では、認識モデルがまだ準備されていません。"
        )
        label = status.label
        note = self.ocr_environment_last_error or status.detail
        icon_name = "alert_circle"
        color = THEME.palette.warning
        primary_text = "状態を確認する"
        primary_command = self.prepare_ocr_environment
        primary_state = "normal"
        secondary_visible = True
        settings_visible = True
        escape_command = self.defer_ocr_setup
        if self.ocr_environment_restart_required:
            title = "OCRモデルの保存先を確認"
            subtitle = "変更した保存先を使用するには、アプリの再起動が必要です。"
            label = "再起動が必要"
            note = "変更したモデル保存先は、アプリの再起動後に使用できます。"
            primary_text = "アプリを再起動"
            primary_command = self.restart_for_ocr_cache_change
        elif status.state == OCR_ENV_SETUP:
            primary_text = "ダウンロードして準備"
        elif status.state == OCR_ENV_VERIFY:
            title = "OCR認識モデルを確認"
            subtitle = (
                "保存済みの認識モデルを現在の実行環境で確認します。\n"
                "不足している場合だけ追加で取得します。"
            )
            primary_text = "確認して更新"
        elif status.state == OCR_ENV_LOCATION_ERROR:
            title = "OCRモデルの保存先を確認"
            subtitle = "指定した保存先にアクセスできません。保存先を変更してください。"
            color = THEME.palette.danger
            primary_text = "保存先を確認"
            primary_command = self.open_ocr_environment_settings
            settings_visible = False
        elif status.state == OCR_ENV_UNAVAILABLE:
            title = "OCR実行環境を修復してください"
            subtitle = (
                "「ランチャーで修復」を選ぶとアプリを終了し、\n"
                "PaddleOCRの実行環境を再インストールします。"
            )
            color = THEME.palette.danger
            primary_text = "ランチャーで修復"
            primary_command = self.repair_ocr_runtime
            settings_visible = False
        elif status.state == OCR_ENV_READY:
            title = "OCRの準備が完了しました"
            subtitle = "認識テストとExcel出力を利用できます。"
            icon_name = "check_circle"
            color = THEME.palette.success
            primary_text = "続ける"
            primary_command = self._finish_ocr_setup_success
            secondary_visible = False
            settings_visible = False
            escape_command = self._finish_ocr_setup_success
        if self.ocr_environment_last_error:
            title = "OCR認識モデルを準備できませんでした"
            subtitle = "エラー内容を確認し、もう一度お試しください。"
            if status.state == OCR_ENV_VERIFY:
                primary_text = "もう一度確認"
            elif status.state == OCR_ENV_SETUP:
                primary_text = "もう一度ダウンロード"
        if self.ocr_setup_title_label is not None:
            self.ocr_setup_title_label.configure(text=title)
        if self.ocr_setup_subtitle_label is not None:
            self.ocr_setup_subtitle_label.configure(text=subtitle)
        self.ocr_setup_status_label.configure(
            text=label,
            image=ICONS.get(icon_name, 17, color),
            text_color=color,
        )
        self.ocr_setup_note_label.configure(text=note)
        self.ocr_setup_primary_button.configure(
            text=primary_text,
            command=primary_command,
            state=primary_state,
        )
        if self.ocr_setup_secondary_button is not None:
            self.ocr_setup_secondary_button.configure(
                text=(
                    "今回は認識せず戻る"
                    if self._pending_ocr_action is not None
                    else "今は準備せず、範囲編集へ"
                ),
                command=self.defer_ocr_setup,
                state="normal",
                fg_color=COLOR_SECONDARY,
                hover_color=COLOR_SECONDARY_HOVER,
                border_width=1,
                border_color=COLOR_BORDER,
                text_color=COLOR_TEXT,
            )
            if secondary_visible:
                self.ocr_setup_secondary_button.grid()
            else:
                self.ocr_setup_secondary_button.grid_remove()
        if self.ocr_setup_settings_button is not None:
            if settings_visible:
                self.ocr_setup_settings_button.grid()
            else:
                self.ocr_setup_settings_button.grid_remove()
        if self.ocr_setup_progress is not None:
            self.ocr_setup_progress.stop()
            self.ocr_setup_progress.grid_remove()
            self.ocr_setup_note_label.grid_configure(pady=(0, 12))
            self.ocr_setup_primary_button.grid()
        if self.ocr_setup_modal is not None:
            self.ocr_setup_modal.panel.configure(border_color=color)
            focus_order = [
                self.ocr_setup_primary_button,
            ]
            if (
                secondary_visible
                and self.ocr_setup_secondary_button is not None
            ):
                focus_order.append(self.ocr_setup_secondary_button)
            if settings_visible and self.ocr_setup_settings_button is not None:
                focus_order.append(self.ocr_setup_settings_button)
            self.ocr_setup_modal.set_focus_order(
                [widget for widget in focus_order if widget is not None],
                default=self.ocr_setup_primary_button,
            )
            self.ocr_setup_modal.set_escape_handler(escape_command)
            self.ocr_setup_modal.refresh_geometry()
        self._refresh_ocr_readiness_banner(status)

    def _show_ocr_setup_progress(self) -> bool:
        """Keep the OCR setup gate visible while model preparation runs."""
        if (
            self.ocr_setup_modal is None
            or self.ocr_setup_status_label is None
            or self.ocr_setup_note_label is None
            or self.ocr_setup_primary_button is None
            or self.ocr_setup_secondary_button is None
        ):
            return False
        if self.ocr_setup_title_label is not None:
            self.ocr_setup_title_label.configure(
                text="OCR認識モデルを準備しています"
            )
        if self.ocr_setup_subtitle_label is not None:
            self.ocr_setup_subtitle_label.configure(
                text=(
                    "必要な認識モデルを確認して動作確認しています。\n"
                    "不足している場合だけダウンロードします。"
                )
            )
        self.ocr_setup_status_label.configure(
            text="準備中",
            image=ICONS.get("refresh", 17, THEME.palette.info),
            text_color=THEME.palette.info,
        )
        self.ocr_setup_note_label.configure(
            text="モデル保存先を確認しています。"
        )
        self.ocr_setup_note_label.grid_configure(pady=(0, 12))
        self.ocr_setup_primary_button.grid_remove()
        self.ocr_setup_secondary_button.configure(
            text="準備をキャンセル",
            command=self.confirm_cancel_current_operation,
            state="normal",
            fg_color="transparent",
            hover_color=COLOR_UTILITY_HOVER,
            border_width=0,
            text_color=COLOR_MUTED,
        )
        self.ocr_setup_secondary_button.grid()
        if self.ocr_setup_settings_button is not None:
            self.ocr_setup_settings_button.grid_remove()
        if self.ocr_setup_progress is not None:
            self.ocr_setup_progress.grid()
            self.ocr_setup_progress.start()
        self.ocr_setup_modal.panel.configure(
            border_color=THEME.palette.info
        )
        self.ocr_setup_modal.set_focus_order(
            [self.ocr_setup_secondary_button],
            default=self.ocr_setup_secondary_button,
        )
        self.ocr_setup_modal.set_escape_handler(
            self.confirm_cancel_current_operation
        )
        self.ocr_setup_modal.refresh_geometry()
        self.ocr_setup_modal.show(focus=self.ocr_setup_secondary_button)
        return True

    def _refresh_open_ocr_environment_settings(self) -> None:
        refresher = self._ocr_environment_ui_refresher
        if refresher is not None:
            refresher()

    def defer_ocr_setup(self) -> None:
        logger.info("OCR setup deferred by user")
        self._pending_ocr_action = None
        if self.ocr_setup_modal is not None:
            self.ocr_setup_modal.hide()
        if not self.workflow_started:
            self._show_workflow_choice()
        else:
            self.status_var.set(
                "OCRは未準備です。範囲編集は続けられます。"
            )
        self._refresh_ocr_readiness_banner()

    def open_ocr_environment_settings(self) -> None:
        if self.ocr_setup_modal is not None:
            self.ocr_setup_modal.hide()
        self._open_ocr_environment_modal(
            after_close=self._return_from_ocr_environment_settings,
        )

    def _return_from_ocr_environment_settings(self) -> None:
        if self.ocr_environment.quick_status().ready:
            self._finish_ocr_setup_success()
        else:
            self.show_ocr_setup()

    def prepare_ocr_environment(self) -> None:
        if self.busy:
            return
        status = self.ocr_environment.quick_status()
        if status.state in {OCR_ENV_LOCATION_ERROR, OCR_ENV_UNAVAILABLE}:
            self._refresh_ocr_setup_overlay()
            return
        self.ocr_environment_last_error = ""
        language = self.lang_var.get()
        progress_visible = self._show_ocr_setup_progress()

        def work() -> OcrEnvironmentStatus:
            def show_phase(phase: OcrSetupPhase) -> None:
                try:
                    self.root.after(
                        0,
                        lambda message=phase.message: (
                            self.ocr_setup_note_label.configure(text=message)
                            if self.ocr_setup_note_label is not None
                            else None
                        ),
                    )
                except Exception:
                    logger.debug("OCR setup progress update was discarded")

            self.ocr_setup_runner.run(
                language=language,
                cache_dir=self.ocr_environment.cache_dir,
                cancel_check=self._is_operation_cancelled,
                on_phase=show_phase,
            )
            self._raise_if_operation_cancelled()
            return self.ocr_environment.record_verified()

        def on_success(_status: OcrEnvironmentStatus) -> None:
            self.status_var.set("OCRの準備が完了しました。")

        def on_error(error: Exception) -> None:
            self.ocr_environment.invalidate_verification()
            self.ocr_environment_last_error = f"準備に失敗しました: {error}"
            self.status_var.set("OCRを準備できませんでした。")

        def finish() -> None:
            self._ocr_setup_operation = False
            self._refresh_ocr_readiness_banner()
            self._refresh_open_ocr_environment_settings()
            self.show_ocr_setup()

        self._ocr_setup_operation = True
        self._run_background(
            "OCR認識モデルを準備しています",
            work,
            on_success,
            on_error,
            lambda: self.root.after(50, finish),
            lambda: self.status_var.set("OCRの準備をキャンセルしました。"),
            show_loading_overlay=not progress_visible,
        )

    def repair_ocr_runtime(self) -> None:
        if self.busy:
            return
        if not self.dialogs.askyesno(
            "OCR実行環境を修復",
            "アプリを終了し、専用ランチャーでPaddleOCR実行環境を"
            "再インストールします。\n\n"
            "OCR認識モデル、テンプレート、端末設定は削除しません。"
            "ネットワーク接続が必要です。",
            yes_text="アプリを終了して修復",
            no_text="戻る",
        ):
            return
        if not self._confirm_unsaved_changes("修復のために終了する"):
            return
        project_root = Path(__file__).resolve().parents[1]
        try:
            launch_repair_handoff(project_root)
        except (LauncherError, OSError) as error:
            logger.exception("Failed to hand off OCR runtime repair")
            self.dialogs.showerror(
                "ランチャーを起動できません",
                f"実行環境の修復を開始できませんでした。\n\n{error}",
            )
            return
        logger.info("OCR runtime repair handed off to launcher")
        self.root.destroy()

    def restart_for_ocr_cache_change(self) -> None:
        if self.busy:
            return
        if not self._confirm_unsaved_changes("再起動する"):
            return
        project_root = Path(__file__).resolve().parents[1]
        try:
            launch_application_handoff(project_root)
        except (LauncherError, OSError) as error:
            logger.exception("Failed to restart app through launcher")
            self.dialogs.showerror(
                "アプリを再起動できません",
                f"ランチャーからアプリを再起動できませんでした。\n\n{error}",
            )
            return
        logger.info("App restart handed off to launcher")
        self.root.destroy()

    def _finish_ocr_setup_success(self) -> None:
        if self.ocr_setup_modal is not None:
            self.ocr_setup_modal.hide()
        pending_action = self._pending_ocr_action
        self._pending_ocr_action = None
        self._refresh_ocr_readiness_banner()
        if pending_action is not None:
            self.root.after(0, pending_action)
        elif not self.workflow_started:
            self._show_workflow_choice()

    def _require_ocr_ready(self, pending_action) -> bool:
        if (
            self.ocr_environment.quick_status().ready
            and not self.ocr_environment_restart_required
        ):
            return True
        self.show_ocr_setup(pending_action)
        return False

    def open_image_folder(self) -> bool:
        if not self.workflow_started:
            self._show_workflow_choice()
            self.status_var.set("最初に処理方法を選択してください。")
            return False
        folder_name = filedialog.askdirectory(title="画像フォルダを選択")
        if not folder_name:
            return False
        folder = Path(folder_name)
        files = sorted(
            (
                path
                for path in folder.iterdir()
                if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
            ),
            key=natural_image_sort_key,
        )
        if not files:
            self.dialogs.showinfo(
                "画像なし", "選択したフォルダ内に対応画像がありません。"
            )
            return False
        self.source_folder = folder
        self.image_files = files
        self.image_queue.reset(files)
        logger.info("Image folder selected | images=%d", len(files))
        self._clear_retry_context()
        self.folder_var.set(self._folder_status_text())
        self.current_image_index = 0
        self._load_current_image(auto_ocr=False)
        if self.fields and self.mode_var.get() == MODE_TEMPLATE:
            self.mode_var.set(MODE_REVIEW)
        self._hide_workflow_choice()
        self._render_side_body()
        return True

    def previous_image(self, _event=None) -> None:
        if len(self.image_files) <= 1:
            return
        self.current_image_index = (self.current_image_index - 1) % len(
            self.image_files
        )
        self._load_current_image(auto_ocr=False)
        self._render_side_body()

    def next_image(self, _event=None) -> None:
        if len(self.image_files) <= 1:
            return
        self.current_image_index = (self.current_image_index + 1) % len(
            self.image_files
        )
        self._load_current_image(auto_ocr=False)
        self._render_side_body()

    def _load_current_image(self, auto_ocr: bool) -> None:
        if not (0 <= self.current_image_index < len(self.image_files)):
            return
        self.image_path = self.image_files[self.current_image_index]
        try:
            self.original_image = Image.open(self.image_path).convert("RGB")
        except Exception as exc:
            logger.exception("Failed to open image")
            self.dialogs.showerror("画像エラー", f"画像を開けませんでした。\n{exc}")
            return
        self._refresh_content_mapping()
        if self.auto_detection_enabled:
            self._apply_auto_detection_preview()
        else:
            fill_missing_field_source_size(self.fields, self.original_image.size)
        self.zoom = self._initial_zoom()
        self.current_results = [""] * len(self.fields)
        self.current_raw_results = [""] * len(self.fields)
        self.image_var.set(self._image_status_text())
        self.image_count_var.set(
            f"{self.current_image_index + 1} / {len(self.image_files)}"
        )
        self.selected_index = self.selected_index if self.fields else None
        self.canvas_edit_state = None
        self.undo_action = None
        self.redraw()
        if auto_ocr:
            self._show_loading(
                "OCRを準備しています。初回はモデル読み込みに時間がかかります。"
            )
            try:
                self._ocr_all_current(show_errors=False)
            finally:
                self._hide_loading()
        if auto_ocr:
            self.status_var.set(
                "画像を読み込みました。認識テストの結果を確認できます。"
            )
        else:
            if self.auto_detection_enabled:
                labels = {
                    **self.app_config.copy.detection_statuses,
                    "log": (
                        f"{self.set_definition.name}を "
                        f"{self.current_detection_count} 件検出しました。"
                    ),
                    "supplement_candidate": self.current_detection_reason,
                    "unknown": self.app_config.copy.detection_failure_status,
                }
                status = labels.get(
                    self.current_detection_layout,
                    self.app_config.copy.detection_fallback_status,
                )
                if self.current_detection_layout == "unknown":
                    self._set_warning_status(status)
                else:
                    self.status_var.set(status)
            else:
                self.status_var.set(
                    "画像を読み込みました。認識テストで代表画像を試せます。"
                )

    def _reset_status_text_color(self, *_args) -> None:
        label = getattr(self, "status_label", None)
        if label is not None:
            label.configure(text_color=THEME.palette.toolbar_text)

    def _set_warning_status(self, message: str) -> None:
        self.status_var.set(message)
        if self.status_label is not None:
            self.status_label.configure(text_color=THEME.palette.warning)

    def _apply_auto_detection_preview(self) -> None:
        strategy = self.profile.auto_detection
        if self.original_image is None or strategy is None:
            return
        prototypes = [replace(field) for field in self.auto_detection_base_fields]
        detection = strategy.detect(
            self.original_image,
            prototypes,
            self.coordinate_settings,
        )
        self.current_detection_layout = detection.layout
        self.current_detection_reason = detection.review_reason
        self.current_detection_count = len(detection.groups)
        if not detection.groups:
            self.fields = prototypes
            self.selected_index = None
            return
        detected_fields: list[TemplateField] = []
        multiple = len(detection.groups) > 1
        for set_id, slots in detection.groups:
            detected_fields.extend(
                replace(
                    field,
                    name=f"{field.name}{set_id}" if multiple else field.name,
                )
                for field in (
                    slots[column.key]
                    for column in self.set_definition.columns
                    if column.key in slots
                )
            )
        detected_fields.extend(replace(field) for field in detection.candidate_fields)
        self.fields = detected_fields
        self.selected_index = (
            len(detected_fields) - 1 if detection.candidate_fields else 0
        )

    def _refresh_content_mapping(self) -> None:
        self.current_content_rect = None
        if (
            self.original_image is None
            or self.coordinate_settings.coordinate_space != COORDINATE_SPACE_CONTENT
        ):
            self._update_coordinate_status_display()
            return
        detected = detect_content_rect(self.original_image, self.coordinate_settings)
        if detected is None:
            self._update_coordinate_status_display()
            return
        self.current_content_rect = detected
        if self.coordinate_settings.source_content_rect is None or not self.fields:
            self.coordinate_settings = replace(
                self.coordinate_settings, source_content_rect=detected
            )
        self._update_coordinate_status_display()

    def _scaled_field(self, field: TemplateField) -> TemplateField:
        return scaled_field(
            field,
            self.original_image,
            self.coordinate_settings,
            self.current_content_rect,
        )

    def _adopt_current_image_as_coordinate_source(self) -> None:
        if self.original_image is None:
            return
        target_width, target_height = self.original_image.size
        source_rect_matches = (
            self.coordinate_settings.coordinate_space != COORDINATE_SPACE_CONTENT
            or self.coordinate_settings.source_content_rect == self.current_content_rect
        )
        if (
            all(
                field.source_width == target_width
                and field.source_height == target_height
                for field in self.fields
            )
            and source_rect_matches
        ):
            return
        mapped_fields = [
            self._scaled_field(field).normalized() for field in self.fields
        ]
        for field, mapped in zip(self.fields, mapped_fields):
            field.x1, field.y1, field.x2, field.y2 = (
                mapped.x1,
                mapped.y1,
                mapped.x2,
                mapped.y2,
            )
            field.source_width, field.source_height = target_width, target_height
        if (
            self.coordinate_settings.coordinate_space == COORDINATE_SPACE_CONTENT
            and self.current_content_rect
        ):
            self.coordinate_settings = replace(
                self.coordinate_settings, source_content_rect=self.current_content_rect
            )
        elif self.coordinate_settings.coordinate_space == COORDINATE_SPACE_CONTENT:
            self.coordinate_settings = replace(
                self.coordinate_settings,
                coordinate_space=COORDINATE_SPACE_IMAGE,
                source_content_rect=None,
            )
        self._mark_dirty()

    def _image_status_text(self) -> str:
        if not self.image_path:
            return "画像未選択"
        return self.image_path.name

    def _folder_status_text(self) -> str:
        if not self.source_folder:
            return "未選択"
        return f"{self.source_folder.name} / {len(self.image_files)}画像"

    def select_output_file(self) -> None:
        file_name = filedialog.asksaveasfilename(
            title="Excel出力先を選択",
            defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx")],
        )
        if not file_name:
            return
        self.output_path = Path(file_name)
        self.output_var.set(self.output_path.name)
        self._clear_retry_context(reset_queue=True)
        self._render_side_body()

    def save_template(self, _event=None) -> bool:
        return self._save_template_document(force_path_selection=False)

    def save_template_as(self, _event=None) -> bool:
        return self._save_template_document(force_path_selection=True)

    def _save_template_document(self, *, force_path_selection: bool) -> bool:
        if not self.fields:
            self.dialogs.showinfo("項目なし", "保存する読み取り項目がありません。")
            return False
        if self.template_path and not force_path_selection:
            path = self.template_path
        else:
            initial_path = self.template_path
            file_name = filedialog.asksaveasfilename(
                title=(
                    "テンプレートに名前を付けて保存"
                    if force_path_selection
                    else "テンプレートを保存"
                ),
                initialdir=(
                    str(initial_path.parent) if initial_path is not None else None
                ),
                initialfile=(
                    initial_path.name
                    if initial_path is not None
                    else self.app_config.default_template_name
                ),
                defaultextension=".json",
                filetypes=[("JSON", "*.json")],
            )
            if not file_name:
                return False
            path = Path(file_name)
        if self.original_image:
            fill_missing_field_source_size(self.fields, self.original_image.size)
        self._normalize_fields_to_profile_backend(self.fields)
        data = build_template_data(
            fields=self.fields,
            lang=self.lang_var.get(),
            ocr_backend=self.profile.default_backend,
            output_settings=self._export_settings_dict(),
            correction_rules=self.correction_rules,
            coordinate_settings=self.coordinate_settings,
            text_formatting=self.text_formatting,
            profile_options={
                "image_text_correction": self.image_text_correction_enabled,
            },
            set_definition=self.set_definition,
            template_name=path.stem,
            profile_id=self.profile.profile_id,
        )
        try:
            save_template(path, data)
        except Exception as exc:
            logger.exception("Failed to save template")
            self.dialogs.showerror(
                "テンプレート保存エラー",
                f"テンプレートを保存できませんでした。元のファイルは変更されていません。\n\n{path}\n{exc}",
            )
            return False
        self.template_path = path
        self._mark_clean()
        logger.info("Template saved | fields=%d", len(self.fields))
        self.status_var.set(f"テンプレートを保存しました: {path.name}")
        return True

    def load_template(self, _event=None) -> None:
        file_name = filedialog.askopenfilename(
            title="読み込むテンプレートを選択",
            filetypes=[("JSON", "*.json"), ("すべてのファイル", "*.*")],
        )
        if not file_name:
            return
        path = Path(file_name)
        try:
            data = load_template(path, expected_profile_id=self.profile.profile_id)
            fields = fields_from_template(data)
        except Exception as exc:
            logger.exception("Failed to load template")
            self.dialogs.showerror(
                "テンプレートエラー", f"テンプレートを読み込めませんでした。\n{exc}"
            )
            return
        if not fields:
            self.dialogs.showerror("テンプレートエラー", "読み取り項目がありません。")
            return
        backend_error = self._template_backend_error(data, fields)
        if backend_error:
            self.dialogs.showerror("テンプレートエラー", backend_error)
            return
        self._normalize_fields_to_profile_backend(fields)
        if not self._confirm_unsaved_changes("別のテンプレートを読み込む"):
            return
        with self._without_dirty_tracking():
            self.workflow_started = True
            self.auto_detection_enabled = False
            self.auto_detection_base_fields = []
            self.empty_set_ids = set()
            self.fields = fields
            self.set_definition = set_definition_from_template(
                data,
                self.profile.set_definitions,
            )
            self.set_preset_var.set(self.set_definition.name)
            self.pending_set_id = None
            self.pending_slot_key = ""
            self.correction_rules = correction_rules_from_template(data)
            self.coordinate_settings = coordinate_settings_from_template(data)
            self.text_formatting = text_formatting_from_template(
                data, self.profile.text_formatting_settings()
            )
            profile_options = profile_options_from_template(data)
            self.image_text_correction_enabled = bool(
                profile_options.get(
                    "image_text_correction",
                    self.profile.image_text_corrector is not None,
                )
                and self.profile.image_text_corrector is not None
            )
            self.ocr_engine.image_text_corrector = (
                self.profile.image_text_corrector
                if self.image_text_correction_enabled
                else None
            )
            self._refresh_content_mapping()
            self.current_results = [""] * len(self.fields)
            self.current_raw_results = [""] * len(self.fields)
            self.selected_index = 0
            self.undo_action = None
            self.template_path = path
            self.lang_var.set(data.get("lang") or self.profile.default_lang)
            self.lang_display_var.set(self._lang_display(self.lang_var.get()))
            self._apply_output_settings(data.get("output_settings") or {})
        logger.info("Template loaded | fields=%d", len(fields))
        self._mark_clean()
        self._hide_workflow_choice()
        self.status_var.set(f"テンプレートを読み込みました: {path.name}")
        self._render_side_body()
        self.redraw()

    def _template_backend_error(
        self, data: dict, fields: list[TemplateField]
    ) -> str:
        backend = self.profile.default_backend
        template_backend = str(data.get("ocr_backend") or backend)
        field_backends = {
            field.ocr_backend
            for field in fields
            if field.ocr_backend not in {"", "default", backend}
        }
        if template_backend == backend and not field_backends:
            return ""
        backend_name = "PaddleOCR" if backend == "paddle" else backend
        return (
            f"このアプリでは{backend_name}用テンプレートだけを読み込めます。"
        )

    @staticmethod
    def _normalize_fields_to_profile_backend(fields: list[TemplateField]) -> None:
        for field in fields:
            field.ocr_backend = "default"

    @staticmethod
    def _field_for_ocr(field: TemplateField) -> TemplateField:
        return replace(field, ocr_backend="default")

    def open_diagnostic_log_folder(self) -> None:
        try:
            log_dir = ensure_log_directory()
            startfile = getattr(os, "startfile")
            startfile(str(log_dir))
            logger.info("Diagnostic log directory opened")
        except (AttributeError, OSError) as exc:
            logger.exception("Failed to open diagnostic log directory")
            self.dialogs.showerror(
                "ログを開けません",
                f"診断ログの保存先を開けませんでした。\n{exc}",
            )

    def open_settings_modal(self, *, after_close=None) -> None:
        if self.settings_modal is not None and self.settings_modal.visible:
            self.settings_modal.show()
            return
        modal = ModalOverlay(
            self.root,
            width=480,
            backdrop_color=THEME.palette.modal_backdrop,
            backdrop_alpha=THEME.layout.modal_backdrop_alpha,
            surface_color=COLOR_SURFACE,
            border_color=COLOR_BORDER,
            corner_radius=THEME.layout.panel_radius,
        )
        self.settings_modal = modal

        def close_modal() -> None:
            if self.settings_modal is modal:
                self.settings_modal = None
            modal.destroy()
            if after_close is not None:
                self.root.after(0, after_close)

        window = modal.window
        dialog_lang_display = StringVar(
            window, value=self._lang_display(self.lang_var.get())
        )
        dialog_line_join = StringVar(
            window, value=LINE_JOIN_DISPLAY[self.text_formatting.line_join]
        )
        dialog_fullwidth_ascii = BooleanVar(
            window, value=self.text_formatting.fullwidth_ascii
        )
        dialog_image_text_correction = BooleanVar(
            window, value=self.image_text_correction_enabled
        )

        panel = modal.panel
        panel.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            panel,
            text="",
            image=ICONS.get("settings", 24, COLOR_PRIMARY),
            width=28,
            height=28,
        ).grid(row=0, column=0, sticky="nw", padx=(26, 10), pady=(25, 0))
        ctk.CTkLabel(
            panel,
            text="基本設定",
            anchor="w",
            justify="left",
            font=UI_FONT_TITLE,
            text_color=COLOR_TEXT,
        ).grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(24, 0))
        close_button = ctk.CTkButton(
            panel,
            text="",
            image=ICONS.get("x", 16, COLOR_MUTED),
            command=close_modal,
            width=28,
            height=28,
            corner_radius=THEME.layout.control_radius,
            fg_color="transparent",
            hover_color=THEME.palette.modal_button_hover,
            border_width=0,
        )
        close_button.grid(
            row=0, column=2, sticky="ne", padx=(0, 12), pady=(12, 0)
        )
        add_tooltip(close_button, "閉じる")

        content = ctk.CTkFrame(panel, fg_color="transparent")
        content.grid(
            row=1,
            column=0,
            columnspan=3,
            sticky="ew",
            padx=26,
            pady=(20, 24),
        )
        content.grid_columnconfigure(0, weight=1)
        ocr_language_label = ctk.CTkLabel(
            content,
            text="OCR言語",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_TEXT,
        )
        ocr_language_label.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        lang_combo = ctk.CTkComboBox(
            content,
            variable=dialog_lang_display,
            values=["日本語 + English", "日本語のみ", "English のみ"],
            height=THEME.layout.entry_height,
            font=UI_FONT,
            dropdown_font=UI_FONT,
            fg_color=COLOR_INPUT,
            border_color=COLOR_BORDER,
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_PRIMARY_HOVER,
            text_color=COLOR_TEXT,
        )
        lang_combo.grid(row=1, column=0, sticky="ew")
        environment_row = ctk.CTkFrame(content, fg_color="transparent")
        environment_row.grid(row=2, column=0, sticky="ew", pady=(18, 0))
        environment_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            environment_row,
            text="OCR環境",
            anchor="w",
            font=UI_FONT_BOLD,
            text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w")
        environment_status_label = ctk.CTkLabel(
            environment_row,
            text="",
            anchor="e",
            compound="left",
            font=UI_FONT_SMALL,
        )
        environment_status_label.grid(row=0, column=1, sticky="e", padx=(12, 8))
        environment_button = ctk.CTkButton(
            environment_row,
            text="環境設定",
            image=ICONS.get("chevron_right", 14, COLOR_MUTED),
            compound="right",
            width=96,
            height=30,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            border_width=1,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        )
        environment_button.grid(row=0, column=2, sticky="e")

        ctk.CTkLabel(
            content,
            text="テキスト整形（全項目共通）",
            anchor="w",
            font=UI_FONT_BOLD,
            text_color=COLOR_TEXT,
        ).grid(row=3, column=0, sticky="ew", pady=(20, 8))
        ctk.CTkLabel(
            content,
            text="行のつなぎ方",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_TEXT,
        ).grid(row=4, column=0, sticky="ew", pady=(0, 6))
        line_join_menu = ctk.CTkOptionMenu(
            content,
            variable=dialog_line_join,
            values=list(LINE_JOIN_DISPLAY.values()),
            height=THEME.layout.entry_height,
            font=UI_FONT,
            fg_color=COLOR_SECONDARY,
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_PRIMARY_HOVER,
            dropdown_fg_color=COLOR_SURFACE_ALT,
            dropdown_hover_color=COLOR_SECONDARY_HOVER,
            text_color=COLOR_TEXT,
        )
        line_join_menu.grid(row=5, column=0, sticky="ew")
        fullwidth_checkbox = ctk.CTkCheckBox(
            content,
            text="半角の英数字・記号を全角に統一",
            variable=dialog_fullwidth_ascii,
            font=UI_FONT_SMALL,
            text_color=COLOR_TEXT,
            checkbox_width=18,
            checkbox_height=18,
            corner_radius=4,
            border_width=1,
            border_color=COLOR_BORDER,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_PRIMARY_HOVER,
        )
        fullwidth_checkbox.grid(row=6, column=0, sticky="w", pady=(14, 0))
        image_text_correction_checkbox = None
        note_row = 7
        if self.profile.image_text_corrector is not None:
            image_text_correction_checkbox = ctk.CTkCheckBox(
                content,
                text="横線記号（一・ー・――）を画像形状で補正",
                variable=dialog_image_text_correction,
                font=UI_FONT_SMALL,
                text_color=COLOR_TEXT,
                checkbox_width=18,
                checkbox_height=18,
                corner_radius=4,
                border_width=1,
                border_color=COLOR_BORDER,
                fg_color=COLOR_PRIMARY,
                hover_color=COLOR_PRIMARY_HOVER,
            )
            image_text_correction_checkbox.grid(
                row=7, column=0, sticky="w", pady=(12, 0)
            )
            add_tooltip(
                image_text_correction_checkbox,
                "判定できる場合だけ、元画像の横棒の幅と位置から「一」「ー」「――」を補正します。",
            )
            note_row = 8

        footer = ctk.CTkFrame(content, fg_color="transparent")
        footer.grid(row=note_row, column=0, sticky="ew", pady=(24, 0))
        ctk.CTkLabel(
            footer,
            text=f"バージョン {__version__}",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        ).pack(side=LEFT)
        buttons = ctk.CTkFrame(footer, fg_color="transparent")
        buttons.pack(side=RIGHT)
        cancel_button = ctk.CTkButton(
            buttons,
            text="キャンセル",
            command=close_modal,
            width=112,
            height=THEME.layout.primary_button_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            border_width=1,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        )
        cancel_button.pack(side=LEFT)
        apply_button = ctk.CTkButton(
            buttons,
            text="適用",
            command=lambda: self._apply_settings(
                modal,
                dialog_lang_display.get(),
                dialog_line_join.get(),
                dialog_fullwidth_ascii.get(),
                dialog_image_text_correction.get(),
                after_close,
            ),
            width=112,
            height=THEME.layout.primary_button_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_PRIMARY_HOVER,
        )
        apply_button.pack(side=LEFT, padx=(8, 0))

        def refresh_environment_summary() -> None:
            status = self.ocr_environment.quick_status()
            if self.busy:
                icon_name, color, label = "refresh", THEME.palette.info, "準備中"
            elif self.ocr_environment_restart_required:
                icon_name, color, label = (
                    "alert_circle",
                    THEME.palette.warning,
                    "再起動が必要",
                )
            elif status.state == OCR_ENV_READY:
                icon_name, color, label = (
                    "check_circle",
                    THEME.palette.success,
                    status.label,
                )
            elif status.state in {OCR_ENV_SETUP, OCR_ENV_VERIFY}:
                icon_name, color, label = (
                    "alert_circle",
                    THEME.palette.warning,
                    status.label,
                )
            elif status.state in {OCR_ENV_LOCATION_ERROR, OCR_ENV_UNAVAILABLE}:
                icon_name, color, label = (
                    "alert_circle",
                    THEME.palette.danger,
                    status.label,
                )
            else:
                icon_name, color, label = "circle", COLOR_MUTED, status.label
            environment_status_label.configure(
                text=label,
                image=ICONS.get(icon_name, 15, color),
                text_color=color,
            )

        def open_environment() -> None:
            modal.hide()

            def restore_template_settings() -> None:
                if self.settings_modal is not modal:
                    return
                refresh_environment_summary()
                modal.show(focus=environment_button)

            self._open_ocr_environment_modal(
                after_close=restore_template_settings
            )

        environment_button.configure(command=open_environment)
        refresh_environment_summary()
        focus_order = [lang_combo, environment_button, line_join_menu, fullwidth_checkbox]
        if image_text_correction_checkbox is not None:
            focus_order.append(image_text_correction_checkbox)
        focus_order.extend([cancel_button, apply_button, close_button])
        modal.set_focus_order(focus_order, default=lang_combo)
        modal.set_escape_handler(close_modal)
        modal.window.bind(
            "<Control-Return>",
            lambda _event: apply_button.invoke() or "break",
        )
        modal.show(focus=lang_combo)

    def _open_ocr_environment_modal(self, *, after_close=None) -> None:
        if (
            self.ocr_environment_modal is not None
            and self.ocr_environment_modal.visible
        ):
            self.ocr_environment_modal.show()
            return
        modal = ModalOverlay(
            self.root,
            width=480,
            backdrop_color=THEME.palette.modal_backdrop,
            backdrop_alpha=THEME.layout.modal_backdrop_alpha,
            surface_color=COLOR_SURFACE,
            border_color=COLOR_BORDER,
            corner_radius=THEME.layout.panel_radius,
        )
        self.ocr_environment_modal = modal

        def close_modal() -> None:
            if self.ocr_environment_modal is modal:
                self.ocr_environment_modal = None
                self._ocr_environment_ui_refresher = None
            modal.destroy()
            if after_close is not None:
                self.root.after(0, after_close)

        window = modal.window
        dialog_lang_display = StringVar(
            window, value=self._lang_display(self.lang_var.get())
        )
        panel = modal.panel
        panel.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            panel,
            text="",
            image=ICONS.get("settings", 24, COLOR_PRIMARY),
            width=28,
            height=28,
        ).grid(row=0, column=0, sticky="nw", padx=(26, 10), pady=(25, 0))
        ctk.CTkLabel(
            panel,
            text="OCR環境設定",
            anchor="w",
            justify="left",
            font=UI_FONT_TITLE,
            text_color=COLOR_TEXT,
        ).grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(24, 0))
        close_button = ctk.CTkButton(
            panel,
            text="",
            image=ICONS.get("x", 16, COLOR_MUTED),
            command=close_modal,
            width=28,
            height=28,
            corner_radius=THEME.layout.control_radius,
            fg_color="transparent",
            hover_color=THEME.palette.modal_button_hover,
            border_width=0,
        )
        close_button.grid(
            row=0, column=2, sticky="ne", padx=(0, 12), pady=(12, 0)
        )
        add_tooltip(close_button, "閉じる")

        content = ctk.CTkFrame(panel, fg_color="transparent")
        content.grid(
            row=1,
            column=0,
            columnspan=3,
            sticky="ew",
            padx=26,
            pady=(20, 24),
        )
        content.grid_columnconfigure(0, weight=1)
        ocr_language_label = ctk.CTkLabel(
            content,
            text="OCR言語",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_TEXT,
        )
        ocr_language_label.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        lang_combo = ctk.CTkComboBox(
            content,
            variable=dialog_lang_display,
            values=["日本語 + English", "日本語のみ", "English のみ"],
            height=THEME.layout.entry_height,
            font=UI_FONT,
            dropdown_font=UI_FONT,
            fg_color=COLOR_INPUT,
            border_color=COLOR_BORDER,
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_PRIMARY_HOVER,
            text_color=COLOR_TEXT,
        )
        lang_combo.grid(row=1, column=0, sticky="ew")

        environment_row = ctk.CTkFrame(content, fg_color="transparent")
        environment_row.grid(row=2, column=0, sticky="ew", pady=(18, 0))
        environment_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            environment_row,
            text="OCR環境",
            anchor="w",
            font=UI_FONT_BOLD,
            text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w")
        environment_status_label = ctk.CTkLabel(
            environment_row,
            text="",
            anchor="e",
            compound="left",
            font=UI_FONT_SMALL,
        )
        environment_status_label.grid(row=0, column=1, sticky="e", padx=(12, 8))

        environment_details = ctk.CTkFrame(
            content,
            fg_color=COLOR_SURFACE_ALT,
            border_width=1,
            border_color=COLOR_BORDER,
            corner_radius=THEME.layout.control_radius,
        )
        environment_details.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        environment_details.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            environment_details,
            text="OCR方式",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        ).grid(row=0, column=0, sticky="w", padx=(14, 12), pady=(12, 4))
        ctk.CTkLabel(
            environment_details,
            text=self.app_config.copy.fixed_ocr_backend_label,
            anchor="e",
            font=UI_FONT_SMALL,
            text_color=COLOR_TEXT,
        ).grid(row=0, column=1, sticky="e", padx=(0, 14), pady=(12, 4))
        ctk.CTkLabel(
            environment_details,
            text="モデル保存先",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        ).grid(row=1, column=0, sticky="w", padx=(14, 12), pady=4)
        cache_mode_label = ctk.CTkLabel(
            environment_details,
            text="",
            anchor="e",
            font=UI_FONT_SMALL,
            text_color=COLOR_TEXT,
        )
        cache_mode_label.grid(row=1, column=1, sticky="e", padx=(0, 14), pady=4)
        cache_path_row = ctk.CTkFrame(
            environment_details,
            height=34,
            fg_color=COLOR_INPUT,
            border_width=1,
            border_color=COLOR_BORDER,
            corner_radius=THEME.layout.control_radius,
        )
        cache_path_row.grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(4, 8)
        )
        cache_path_row.grid_columnconfigure(0, weight=1)
        cache_path_label = ctk.CTkLabel(
            cache_path_row,
            text="",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
        )
        cache_path_label.grid(
            row=0, column=0, sticky="ew", padx=(10, 6), pady=3
        )
        cache_path_tooltip = add_tooltip(cache_path_label, "")
        open_cache_button = ctk.CTkButton(
            cache_path_row,
            text="開く",
            width=48,
            height=26,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color="transparent",
            hover_color=COLOR_UTILITY_HOVER,
            border_width=0,
            text_color=COLOR_TEXT,
        )
        open_cache_button.grid(row=0, column=1, padx=(0, 4), pady=3)
        change_cache_button = ctk.CTkButton(
            cache_path_row,
            text="変更",
            width=52,
            height=26,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            border_width=1,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        )
        change_cache_button.grid(row=0, column=2, padx=(0, 4), pady=3)
        environment_note_label = ctk.CTkLabel(
            environment_details,
            text="",
            anchor="w",
            justify="left",
            font=UI_FONT_SMALL,
            text_color=COLOR_MUTED,
            wraplength=390,
        )
        environment_note_label.grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 10)
        )
        automatic_cache_button = ctk.CTkButton(
            environment_details,
            text="自動設定に戻す",
            height=30,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            border_width=1,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        )
        verify_environment_button = ctk.CTkButton(
            environment_details,
            text="再確認",
            image=ICONS.get("refresh", 15, COLOR_TEXT),
            compound="left",
            height=32,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            border_width=1,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        )
        verify_environment_button.grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 8)
        )
        automatic_cache_button.grid(
            row=5, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 14)
        )
        automatic_cache_button.grid_remove()
        diagnostics_row = ctk.CTkFrame(content, fg_color="transparent")
        diagnostics_row.grid(
            row=3,
            column=0,
            sticky="ew",
            pady=(18, 0),
        )
        diagnostics_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            diagnostics_row,
            text="診断ログ",
            anchor="w",
            font=UI_FONT_SMALL,
            text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w")
        open_log_button = ctk.CTkButton(
            diagnostics_row,
            text="フォルダーを開く",
            command=self.open_diagnostic_log_folder,
            width=120,
            height=30,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_SECONDARY,
            hover_color=COLOR_SECONDARY_HOVER,
            border_width=1,
            border_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
        )
        open_log_button.grid(row=0, column=1, sticky="e")
        add_tooltip(
            open_log_button,
            f"OCR結果は記録しません。保存先: {current_log_path().parent}",
        )
        buttons = ctk.CTkFrame(content, fg_color="transparent")
        buttons.grid(
            row=4,
            column=0,
            sticky="e",
            pady=(24, 0),
        )
        cancel_button = ctk.CTkButton(
            buttons,
            text="完了",
            command=close_modal,
            width=112,
            height=THEME.layout.primary_button_height,
            corner_radius=THEME.layout.control_radius,
            font=UI_FONT_SMALL,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_PRIMARY_HOVER,
            border_width=0,
            text_color=THEME.palette.on_color,
        )
        cancel_button.pack(side=LEFT)

        ocr_language_label.grid_remove()
        lang_combo.grid_remove()
        environment_row.grid_configure(row=0, pady=(0, 0))
        environment_details.grid_configure(row=1, pady=(10, 0))

        def environment_style(
            status: OcrEnvironmentStatus,
        ) -> tuple[str, str]:
            if self.busy:
                return "refresh", THEME.palette.info
            if self.ocr_environment_restart_required:
                return "alert_circle", THEME.palette.warning
            if status.state == OCR_ENV_READY:
                return "check_circle", THEME.palette.success
            if status.state in {OCR_ENV_SETUP, OCR_ENV_VERIFY}:
                return "alert_circle", THEME.palette.warning
            if status.state in {OCR_ENV_LOCATION_ERROR, OCR_ENV_UNAVAILABLE}:
                return "alert_circle", THEME.palette.danger
            return "circle", COLOR_MUTED

        def visible_environment_status() -> tuple[OcrEnvironmentStatus, str, str]:
            status = self.ocr_environment.quick_status()
            if self.busy:
                return (
                    status,
                    "準備中",
                    "認識モデルを確認しています。不足がある場合は取得します。",
                )
            if self.ocr_environment_restart_required:
                return status, "再起動が必要", "保存先は次回起動時に反映されます。"
            note = self.ocr_environment_last_error or status.detail
            return status, status.label, note

        def sync_focus_order() -> None:
            focus_order = [open_cache_button]
            if not self.busy:
                focus_order.extend(
                    [change_cache_button, verify_environment_button]
                )
            if (
                not self.busy
                and self.ocr_environment.settings.cache_mode == "custom"
            ):
                focus_order.append(automatic_cache_button)
            focus_order.extend([open_log_button, cancel_button, close_button])
            modal.set_focus_order(focus_order, default=verify_environment_button)

        def refresh_environment_ui() -> None:
            status, label, note = visible_environment_status()
            icon_name, color = environment_style(status)
            environment_status_label.configure(
                text=label,
                image=ICONS.get(icon_name, 15, color),
                text_color=color,
            )
            cache_mode_label.configure(text=self.ocr_environment.cache_mode_label)
            cache_path = str(status.cache_dir)
            cache_path_label.configure(text=self._compact_path(cache_path))
            cache_path_tooltip.text = cache_path
            environment_note_label.configure(text=note)
            open_cache_button.configure(
                state="normal" if status.cache_dir.is_dir() else "disabled"
            )
            change_cache_button.configure(
                state=(
                    "disabled"
                    if self.busy
                    else "normal"
                )
            )
            verify_environment_button.configure(
                text=(
                    "ランチャーで修復"
                    if status.state == OCR_ENV_UNAVAILABLE
                    else (
                        "ダウンロードして準備"
                        if status.state == OCR_ENV_SETUP
                        else "確認して更新"
                    )
                ),
                command=(
                    self.repair_ocr_runtime
                    if status.state == OCR_ENV_UNAVAILABLE
                    else verify_environment
                ),
                state=(
                    "disabled"
                    if self.busy
                    or self.ocr_environment_restart_required
                    or status.state == OCR_ENV_LOCATION_ERROR
                    else "normal"
                ),
            )
            automatic_cache_button.configure(
                state=(
                    "disabled"
                    if self.busy
                    else "normal"
                )
            )
            if self.ocr_environment.settings.cache_mode == "custom":
                automatic_cache_button.grid()
            else:
                automatic_cache_button.grid_remove()
            sync_focus_order()
            modal.refresh_geometry()

        def open_cache_folder() -> None:
            cache_dir = self.ocr_environment.cache_dir
            if not cache_dir.is_dir():
                self.dialogs.showinfo(
                    "保存先なし", "OCRモデルの保存先はまだ作成されていません。"
                )
                return
            try:
                startfile = getattr(os, "startfile")
                startfile(str(cache_dir))
            except (AttributeError, OSError) as exc:
                logger.exception("Failed to open OCR model directory")
                self.dialogs.showerror(
                    "フォルダーを開けません",
                    f"OCRモデルの保存先を開けませんでした。\n{exc}",
                )

        def cache_change_requires_restart() -> bool:
            target = self.ocr_environment.cache_dir.resolve()
            paddle_loaded = (
                "paddlex" in sys.modules
                or self.ocr_engine.paddle_backend is not None
            )
            if not paddle_loaded:
                self.ocr_environment_active_cache_dir = target
                return False
            return target != self.ocr_environment_active_cache_dir

        def choose_cache_folder() -> None:
            if self.busy:
                return
            current = self.ocr_environment.cache_dir
            initial = current if current.is_dir() else current.parent
            selected = filedialog.askdirectory(
                title="OCRモデルの保存先を選択",
                initialdir=str(initial) if initial.exists() else None,
            )
            if not selected:
                return
            try:
                self.ocr_environment.use_custom_cache(Path(selected))
            except (OSError, ValueError) as exc:
                logger.exception("Failed to change OCR model directory")
                self.dialogs.showerror("保存先エラー", str(exc))
                return
            self.ocr_environment_last_error = ""
            self.ocr_environment_restart_required = cache_change_requires_restart()
            refresh_environment_ui()
            self._refresh_ocr_readiness_banner()

        def reset_automatic_cache() -> None:
            if self.busy:
                return
            try:
                self.ocr_environment.use_automatic_cache()
            except OSError as exc:
                logger.exception("Failed to reset OCR model directory")
                self.dialogs.showerror("保存先エラー", str(exc))
                return
            self.ocr_environment_last_error = ""
            self.ocr_environment_restart_required = cache_change_requires_restart()
            refresh_environment_ui()
            self._refresh_ocr_readiness_banner()

        def verify_environment() -> None:
            if (
                self.busy
                or self.ocr_environment_restart_required
            ):
                return
            self.ocr_environment_last_error = ""
            modal.hide()
            language = self.lang_var.get()

            def work() -> OcrEnvironmentStatus:
                def show_phase(phase: OcrSetupPhase) -> None:
                    try:
                        self.root.after(
                            0,
                            lambda message=phase.message: self.loading_note_var.set(
                                message
                            ),
                        )
                    except Exception:
                        logger.debug("OCR setup progress update was discarded")

                self.ocr_setup_runner.run(
                    language=language,
                    cache_dir=self.ocr_environment.cache_dir,
                    cancel_check=self._is_operation_cancelled,
                    on_phase=show_phase,
                )
                self._raise_if_operation_cancelled()
                return self.ocr_environment.record_verified()

            def on_success(_status: OcrEnvironmentStatus) -> None:
                self.status_var.set("OCR環境を確認しました。")

            def on_error(error: Exception) -> None:
                self.ocr_environment.invalidate_verification()
                self.ocr_environment_last_error = f"確認に失敗しました: {error}"
                self.status_var.set("OCR環境を確認できませんでした。")

            def restore_settings() -> None:
                self._ocr_setup_operation = False
                self._refresh_ocr_readiness_banner()
                if self.ocr_environment_modal is not modal:
                    return
                refresh_environment_ui()
                modal.show(focus=verify_environment_button)

            self._ocr_setup_operation = True
            self._run_background(
                "OCR認識モデルを準備しています",
                work,
                on_success,
                on_error,
                lambda: self.root.after(50, restore_settings),
            )

        open_cache_button.configure(command=open_cache_folder)
        change_cache_button.configure(command=choose_cache_folder)
        automatic_cache_button.configure(command=reset_automatic_cache)
        verify_environment_button.configure(command=verify_environment)
        self._ocr_environment_ui_refresher = refresh_environment_ui
        refresh_environment_ui()

        modal.set_escape_handler(close_modal)
        modal.show(focus=verify_environment_button)

    def _basic_settings_status_message(self, changed: bool) -> str:
        if changed:
            if self.template_path is None:
                return (
                    "設定を適用しました。この設定は未保存です。"
                    "次回も使う場合は、右パネルの［保存］を使用してください。"
                )
            return (
                "設定を適用しました。変更は未保存です。"
                "右パネルの［保存］で上書きしてください。"
            )
        if self.template_path is None:
            return (
                "設定は変更されていません（未保存）。"
                "次回も使う場合は、右パネルの［保存］を使用してください。"
            )
        if self.dirty:
            return (
                "設定は変更されていません。"
                "ほかに未保存の変更があります。"
            )
        return "設定は変更されていません（保存済み）。"

    def _apply_settings(
        self,
        window: ModalOverlay,
        lang_display: str,
        line_join_display: str,
        fullwidth_ascii_enabled: bool,
        image_text_correction_enabled: bool | None = None,
        after_close=None,
    ) -> None:
        lang = self._lang_value(lang_display)
        line_join = next(
            (
                value
                for value, display in LINE_JOIN_DISPLAY.items()
                if display == line_join_display
            ),
            LINE_JOIN_FULLWIDTH_SPACE,
        )
        text_formatting = TextFormattingSettings(
            line_join=line_join,
            fullwidth_ascii=fullwidth_ascii_enabled,
        ).normalized()
        correction_enabled = bool(
            (
                self.image_text_correction_enabled
                if image_text_correction_enabled is None
                else image_text_correction_enabled
            )
            and self.profile.image_text_corrector is not None
        )
        changed = (lang, text_formatting, correction_enabled) != (
            self.lang_var.get(),
            self.text_formatting,
            self.image_text_correction_enabled,
        )
        with self._without_dirty_tracking():
            self.lang_var.set(lang)
            self.lang_display_var.set(self._lang_display(lang))
            self.text_formatting = text_formatting
            self.image_text_correction_enabled = correction_enabled
            self.ocr_engine.image_text_corrector = (
                self.profile.image_text_corrector
                if correction_enabled
                else None
            )
        if changed:
            self.current_results = [""] * len(self.fields)
            self.current_raw_results = [""] * len(self.fields)
            self._mark_dirty()
        self.status_var.set(self._basic_settings_status_message(changed))
        window.destroy()
        if self.settings_modal is window:
            self.settings_modal = None
        if changed:
            self._render_side_body()
        if after_close is not None:
            self.root.after(0, after_close)

    def select_field(self, idx: int) -> None:
        if not (0 <= idx < len(self.fields)):
            return
        self._select_field_index(idx, update_review_detail=True)

    def _select_field_index(
        self, idx: int, update_review_detail: bool, redraw_canvas: bool = True
    ) -> None:
        if not (0 <= idx < len(self.fields)):
            return
        if self.editing_name_index is not None and self.editing_name_index != idx:
            if not self._commit_active_name_edit():
                return
        if self.selected_index == idx:
            return
        self.selected_index = idx
        self._refresh_selection_rows()
        if update_review_detail and self.mode_var.get() == MODE_REVIEW:
            self._refresh_review_detail_panel()
        if redraw_canvas:
            self.redraw()

    def _refresh_selection_rows(self) -> None:
        for idx, row in list(self.field_row_by_index.items()):
            try:
                if not row.winfo_exists():
                    continue
                self._refresh_review_row(idx)
            except Exception:
                continue

    def _refresh_review_row(self, idx: int) -> None:
        row = self.field_row_by_index.get(idx)
        if row is not None and row.winfo_exists():
            selected = idx == self.selected_index
            row.configure(
                fg_color=THEME.palette.selected_row if selected else COLOR_SURFACE,
                border_color=COLOR_PRIMARY if selected else COLOR_BORDER,
            )
        order_label = self.review_order_label_by_index.get(idx)
        if order_label is not None and order_label.winfo_exists():
            order_label.configure(
                fg_color=(
                    COLOR_PRIMARY
                    if idx == self.selected_index
                    else THEME.palette.info
                    if self._is_review_candidate(self.fields[idx])
                    else COLOR_SECONDARY
                )
            )
    def _refresh_review_detail_panel(self) -> None:
        if (
            self.review_detail_host is None
            or not self.review_detail_host.winfo_exists()
        ):
            return
        scroll_fraction = self._review_scroll_fraction()
        for child in self.review_detail_host.winfo_children():
            child.destroy()
        self._render_postprocess_panel(self.review_detail_host)
        if scroll_fraction is not None:
            self.root.after_idle(
                lambda fraction=scroll_fraction: self._restore_review_scroll(fraction)
            )

    def _review_scroll_fraction(self) -> float | None:
        scroller = self.review_result_scroller
        if scroller is None or not scroller.winfo_exists():
            return None
        try:
            return float(scroller._parent_canvas.yview()[0])
        except (AttributeError, TclError, IndexError):
            return None

    def _restore_review_scroll(self, fraction: float) -> None:
        scroller = self.review_result_scroller
        if scroller is None or not scroller.winfo_exists():
            return
        try:
            scroller._parent_canvas.yview_moveto(fraction)
        except (AttributeError, TclError):
            return

    def _update_review_detail_values(self) -> None:
        idx = self.selected_index
        if idx is None or not (0 <= idx < len(self.fields)):
            self.review_raw_preview_var.set("未OCR")
            self.review_processed_preview_var.set("未OCR")
            self.review_line_split_var.set(OCR_MODE_OPTIONS[0])
            return
        field = self.fields[idx]
        self._ensure_result_buffers()
        self.review_raw_preview_var.set(
            self._compact_preview_text(self.current_raw_results[idx] or "未OCR")
        )
        self.review_processed_preview_var.set(
            self._compact_preview_text(self.current_results[idx] or "未OCR")
        )
        self.review_line_split_var.set(self._line_split_display(field.ocr_line_split))

    def set_field_enabled(self, idx: int, enabled: bool) -> None:
        if not (0 <= idx < len(self.fields)) or self.fields[idx].enabled == enabled:
            return
        self.fields[idx].enabled = enabled
        self._mark_dirty()
        if self.export_layout_var.get() == EXPORT_LAYOUT_SET:
            self._render_side_body()
        self.redraw()

    def move_selected_field_up(self, _event=None) -> None:
        self.move_selected_field(-1)

    def move_selected_field_down(self, _event=None) -> None:
        self.move_selected_field(1)

    def move_selected_field(self, direction: int) -> None:
        if self.mode_var.get() != MODE_TEMPLATE:
            return
        if not self._commit_active_name_edit():
            return
        idx = self._require_field_selection()
        if idx is None:
            return
        if (
            self.export_layout_var.get() == EXPORT_LAYOUT_SET
            and self.fields[idx].set_id > 0
        ):
            self.move_set(self.fields[idx].set_id, direction)
            return
        self.move_field(idx, direction)

    def move_field(self, idx: int, direction: int) -> None:
        if not (0 <= idx < len(self.fields)):
            return
        self.editing_name_index = None
        new_idx = idx + direction
        if not (0 <= new_idx < len(self.fields)):
            return
        self._ensure_current_results()
        self.fields[idx], self.fields[new_idx] = self.fields[new_idx], self.fields[idx]
        self.current_results[idx], self.current_results[new_idx] = (
            self.current_results[new_idx],
            self.current_results[idx],
        )
        self.current_raw_results[idx], self.current_raw_results[new_idx] = (
            self.current_raw_results[new_idx],
            self.current_raw_results[idx],
        )
        self.selected_index = new_idx
        self._mark_dirty()
        self.status_var.set("項目順を変更しました。Excel出力の列順にも反映されます。")
        self._render_side_body()
        self.redraw()

    def move_field_to(self, source_idx: int, target_idx: int) -> None:
        if not (0 <= source_idx < len(self.fields)):
            return
        target_idx = max(0, min(len(self.fields) - 1, target_idx))
        if source_idx == target_idx:
            self.selected_index = source_idx
            self._render_side_body()
            self.redraw()
            return
        self._ensure_current_results()
        field = self.fields.pop(source_idx)
        result = self.current_results.pop(source_idx)
        raw_result = self.current_raw_results.pop(source_idx)
        self.fields.insert(target_idx, field)
        self.current_results.insert(target_idx, result)
        self.current_raw_results.insert(target_idx, raw_result)
        self.selected_index = target_idx
        self._mark_dirty()
        self.status_var.set("項目順を変更しました。Excel出力の列順にも反映されます。")
        self._render_side_body()
        self.redraw()

    def start_field_drag(self, _event, idx: int) -> str:
        if not (0 <= idx < len(self.fields)):
            return "break"
        self.editing_name_index = None
        self.dragging_field_index = idx
        self.selected_index = idx
        self._set_field_drag_visual(idx, True)
        if self.export_layout_var.get() == EXPORT_LAYOUT_SET:
            self._set_drop_target(self.fields[idx].set_id)
            self.status_var.set(
                f"{self.fields[idx].name} を移動先のセットへドラッグします。"
            )
        else:
            self.status_var.set(f"{self.fields[idx].name} をドラッグして並び替えます。")
        return "break"

    def update_field_drag(self, event, idx: int) -> str:
        if self.dragging_field_index != idx or not (0 <= idx < len(self.fields)):
            return "break"
        if self.export_layout_var.get() == EXPORT_LAYOUT_SET:
            target_set = self._set_drop_id(event.y_root)
            self._set_drop_target(target_set)
            destination = f"セット {target_set}" if target_set > 0 else "未割当"
            self.status_var.set(f"{self.fields[idx].name} を {destination} へ移動")
            return "break"
        target_idx = self._field_drop_index(event.y_root, idx)
        if target_idx != idx:
            self.status_var.set(f"{self.fields[idx].name} を #{target_idx + 1} に移動")
        return "break"

    def finish_field_drag(self, event, idx: int) -> str:
        if self.dragging_field_index != idx:
            return "break"
        self._set_field_drag_visual(idx, False)
        self.dragging_field_index = None
        if self.export_layout_var.get() == EXPORT_LAYOUT_SET:
            target_set = self._set_drop_id(event.y_root)
            self._set_drop_target(None)
            self.move_field_to_set(idx, target_set)
            return "break"
        target_idx = self._field_drop_index(event.y_root, idx)
        self.move_field_to(idx, target_idx)
        return "break"

    def _set_drop_id(self, y_root: int) -> int:
        if not self.set_drop_section_by_id:
            return 0
        sections = list(self.set_drop_section_by_id.items())
        for set_id, section in sections:
            top = section.winfo_rooty()
            if top <= y_root <= top + section.winfo_height():
                return set_id
        return min(
            sections,
            key=lambda item: abs(
                y_root - (item[1].winfo_rooty() + item[1].winfo_height() / 2)
            ),
        )[0]

    def _set_drop_target(self, set_id: int | None) -> None:
        if self.set_drag_target_id == set_id:
            return
        previous_set_id = self.set_drag_target_id
        previous = (
            self.set_drop_section_by_id.get(previous_set_id)
            if previous_set_id is not None
            else None
        )
        if previous is not None:
            previous.configure(fg_color="transparent", border_color=COLOR_SURFACE_ALT)
        self.set_drag_target_id = set_id
        target = (
            self.set_drop_section_by_id.get(set_id)
            if set_id is not None
            else None
        )
        if target is not None:
            target.configure(
                fg_color=THEME.palette.selected_row, border_color=COLOR_PRIMARY
            )

    def move_field_to_set(self, idx: int, target_set_id: int) -> None:
        if not (0 <= idx < len(self.fields)):
            return
        field = self.fields[idx]
        source_set_id = field.set_id
        target_set_id = max(0, target_set_id)
        if source_set_id == target_set_id:
            self.selected_index = idx
            self._render_side_body()
            self.redraw()
            return

        replacement = next(
            (
                candidate
                for candidate_idx, candidate in enumerate(self.fields)
                if candidate_idx != idx
                and candidate.set_id == target_set_id
                and candidate.slot_key == field.slot_key
                and field.slot_key
            ),
            None,
        )
        field.set_id = target_set_id
        if replacement is not None:
            replacement.set_id = source_set_id if source_set_id > 0 else 0

        self.selected_index = idx
        self._mark_dirty()
        destination = f"セット {target_set_id}" if target_set_id > 0 else "未割当"
        if replacement is not None:
            self.status_var.set(
                f"{field.name} を {destination} へ移動し、同じ種類の項目を入れ替えました。"
            )
        else:
            self.status_var.set(f"{field.name} を {destination} へ移動しました。")
        self._render_side_body()
        self.redraw()

    def _set_field_drag_visual(self, idx: int, active: bool) -> None:
        row = self.field_row_by_index.get(idx)
        handle = self.field_drag_handle_by_index.get(idx)
        if row is not None:
            selected = idx == self.selected_index
            row.configure(
                fg_color=(
                    THEME.palette.dragging_row
                    if active
                    else (THEME.palette.selected_row if selected else COLOR_SURFACE)
                ),
                border_color=(
                    THEME.palette.dragging_border
                    if active
                    else (COLOR_PRIMARY if selected else COLOR_BORDER)
                ),
            )
        if handle is not None:
            handle.configure(
                fg_color=COLOR_PRIMARY if active else "transparent",
                corner_radius=THEME.layout.control_radius,
            )

    def _field_drop_index(self, y_root: int, source_idx: int) -> int:
        if len(self.fields) <= 1:
            return source_idx
        target_idx = 0
        for row_idx, row in enumerate(self.field_row_widgets):
            if row_idx == source_idx:
                continue
            center_y = row.winfo_rooty() + (row.winfo_height() / 2)
            if y_root > center_y:
                target_idx += 1
        return max(0, min(len(self.fields) - 1, target_idx))

    def rename_field_at(self, idx: int) -> None:
        self.begin_inline_name_edit(idx)

    def edit_selected_field_name(self, _event=None) -> str:
        if self._focus_is_text_entry():
            return "break"
        if self.mode_var.get() != MODE_TEMPLATE:
            return "break"
        idx = self._require_field_selection()
        if idx is not None:
            self.begin_inline_name_edit(idx)
        return "break"

    def begin_inline_name_edit(self, idx: int) -> str:
        if not (0 <= idx < len(self.fields)):
            return "break"
        self.selected_index = idx
        self.editing_name_index = idx
        self.editing_name_value = self.fields[idx].name
        self.editing_name_error = ""
        self._render_side_body()
        self.redraw()
        self.root.after(20, self._scroll_selected_field_into_view)
        return "break"

    def commit_inline_name_edit(self, idx: int, value: str) -> str:
        if self.editing_name_index != idx or not (0 <= idx < len(self.fields)):
            return "break"
        previous_name = self.fields[idx].name
        name = value.strip()
        if not name:
            self.status_var.set("項目名は変更されませんでした。")
            return self.cancel_inline_name_edit()
        if any(
            field.name == name
            for field_idx, field in enumerate(self.fields)
            if field_idx != idx
        ):
            self.editing_name_value = value
            self.editing_name_error = f"「{name}」は既に使用されています。"
            if (
                self.editing_name_entry is not None
                and self.editing_name_entry.winfo_exists()
            ):
                self.editing_name_entry.configure(border_color=COLOR_DANGER)
                self.editing_name_entry.focus_set()
                self.editing_name_entry.icursor("end")
            if (
                self.editing_name_error_label is not None
                and self.editing_name_error_label.winfo_exists()
            ):
                self.editing_name_error_label.configure(text=self.editing_name_error)
                self.editing_name_error_label.grid()
            return "break"
        self.editing_name_index = None
        self.editing_name_value = ""
        self.editing_name_error = ""
        self.editing_name_entry = None
        self.editing_name_error_label = None
        self.fields[idx].name = name
        self._rename_correction_rule_targets(previous_name, name)
        preset_changed = self._apply_profile_preset_after_rename(idx, previous_name)
        if preset_changed:
            if idx < len(self.current_results):
                self.current_results[idx] = ""
            if idx < len(self.current_raw_results):
                self.current_raw_results[idx] = ""
        if name != previous_name or preset_changed:
            self._mark_dirty()
        self._render_side_body()
        self.redraw()
        return "break"

    def _scroll_selected_field_into_view(self) -> None:
        idx = self.selected_index
        row = self.field_row_by_index.get(idx) if idx is not None else None
        scroller = self.template_fields_scroller
        if (
            row is None
            or scroller is None
            or not row.winfo_exists()
            or not scroller.winfo_exists()
        ):
            return
        try:
            canvas = scroller._parent_canvas
            canvas.update_idletasks()
            content_bbox = canvas.bbox("all")
            content_height = max(1, int(content_bbox[3]))
            row_top = row.winfo_y()
            row_bottom = row_top + row.winfo_height()
            visible_top = cast(float, canvas.canvasy(0))
            visible_bottom = visible_top + canvas.winfo_height()
            if row_top < visible_top:
                canvas.yview_moveto(max(0.0, row_top / content_height))
            elif row_bottom > visible_bottom:
                canvas.yview_moveto(
                    max(0.0, (row_bottom - canvas.winfo_height()) / content_height)
                )
        except (AttributeError, TypeError, IndexError):
            return

    def _rename_correction_rule_targets(self, previous_name: str, name: str) -> None:
        if previous_name == name:
            return
        for rule in self.correction_rules:
            if rule.target == previous_name:
                rule.target = name

    def _apply_profile_preset_after_rename(self, idx: int, previous_name: str) -> bool:
        field = self.fields[idx]
        if not previous_name.startswith(
            "項目"
        ) or not self.profile.has_field_ocr_preset(field.name):
            return False
        preset = self.profile.field_ocr_preset(field.name)
        for key, value in preset.items():
            setattr(field, key, value)
        return True

    def cancel_inline_name_edit(self) -> str:
        self.editing_name_index = None
        self.editing_name_value = ""
        self.editing_name_error = ""
        self.editing_name_entry = None
        self.editing_name_error_label = None
        self._render_side_body()
        self.redraw()
        return "break"

    def _commit_active_name_edit(self) -> bool:
        idx = self.editing_name_index
        if idx is None:
            return True
        value = self.editing_name_value
        if (
            self.editing_name_entry is not None
            and self.editing_name_entry.winfo_exists()
        ):
            value = self.editing_name_entry.get()
        self.commit_inline_name_edit(idx, value)
        return self.editing_name_index is None

    def show_field_context_menu(self, event, idx: int) -> str:
        if not (0 <= idx < len(self.fields)):
            return "break"
        self._select_field_index(idx, update_review_detail=True)
        menu = self._field_actions_menu(idx)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def show_field_actions_menu(self, button, idx: int) -> None:
        if not (0 <= idx < len(self.fields)):
            return
        self._select_field_index(idx, update_review_detail=True)
        menu = self._field_actions_menu(idx)
        try:
            menu.tk_popup(
                button.winfo_rootx(), button.winfo_rooty() + button.winfo_height()
            )
        finally:
            menu.grab_release()

    def _field_actions_menu(self, idx: int, include_undo: bool = False) -> Menu:
        menu = Menu(
            self.root,
            tearoff=0,
            background=COLOR_SURFACE_ALT,
            foreground=COLOR_TEXT,
            activebackground=COLOR_PRIMARY,
            activeforeground=THEME.palette.on_color,
            disabledforeground=THEME.palette.disabled_fill,
            borderwidth=1,
            relief="flat",
        )
        menu.add_command(
            label="項目名を編集    F2", command=lambda i=idx: self.rename_field_at(i)
        )
        menu.add_command(label="範囲を選択", command=lambda i=idx: self.select_field(i))
        menu.add_command(label="複製", command=lambda i=idx: self.duplicate_field_at(i))
        menu.add_separator()
        menu.add_command(
            label="削除", command=lambda i=idx: self.delete_field_at(i, confirm=False)
        )
        if include_undo:
            menu.add_separator()
            menu.add_command(
                label="直前の操作を取り消す",
                command=self.undo_last_action,
                state="normal" if self.undo_action else "disabled",
            )
        return menu

    def show_canvas_context_menu(self, event) -> str:
        if not self.original_image:
            return "break"
        mode = self.mode_var.get()
        if mode == MODE_EXPORT:
            return "break"
        canvas_x, canvas_y = self._event_canvas_point(event)
        label_hit_idx = self._hit_test_label(canvas_x, canvas_y)
        x, y = self._event_image_point(event)
        hit_idx, _mode = (
            (label_hit_idx, "move")
            if label_hit_idx is not None
            else self._hit_test_field(x, y)
        )
        if mode == MODE_REVIEW:
            if hit_idx is not None:
                self._select_field_index(hit_idx, update_review_detail=True)
            return "break"
        if hit_idx is not None:
            self._select_field_index(hit_idx, update_review_detail=True)
            menu = self._field_actions_menu(hit_idx, include_undo=True)
        else:
            menu = Menu(
                self.root,
                tearoff=0,
                background=COLOR_SURFACE_ALT,
                foreground=COLOR_TEXT,
                activebackground=COLOR_PRIMARY,
                activeforeground=THEME.palette.on_color,
                disabledforeground=THEME.palette.disabled_fill,
                borderwidth=1,
                relief="flat",
            )
            menu.add_command(
                label="直前の操作を取り消す",
                command=self.undo_last_action,
                state="normal" if self.undo_action else "disabled",
            )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def duplicate_field_at(self, idx: int) -> None:
        if not (0 <= idx < len(self.fields)):
            return
        source = self.fields[idx]
        duplicate = replace(source)
        duplicate.name = self._unique_field_name(f"{source.name} のコピー")
        if self.export_layout_var.get() == EXPORT_LAYOUT_SET:
            duplicate.set_id = 0
            duplicate.slot_key = ""
        if self.original_image is not None:
            width, height = self.original_image.size
            dx = 12 if duplicate.x2 + 12 <= width else -12 if duplicate.x1 >= 12 else 0
            dy = 12 if duplicate.y2 + 12 <= height else -12 if duplicate.y1 >= 12 else 0
            duplicate.x1 += dx
            duplicate.x2 += dx
            duplicate.y1 += dy
            duplicate.y2 += dy
        insert_idx = idx + 1
        self._ensure_current_results()
        self.fields.insert(insert_idx, duplicate)
        self.current_results.insert(insert_idx, "")
        self.current_raw_results.insert(insert_idx, "")
        self.selected_index = insert_idx
        self.editing_name_index = None
        self._set_undo(
            "add",
            idx=insert_idx,
            field=replace(duplicate),
            field_ref=duplicate,
            result="",
        )
        self._mark_dirty()
        assignment_note = (
            "セット番号とセット内項目は未割当です。"
            if self.export_layout_var.get() == EXPORT_LAYOUT_SET
            else ""
        )
        self.status_var.set(
            f"{duplicate.name} を複製しました。{assignment_note} Esc / Ctrl+Z で戻せます。"
        )
        self._render_side_body()
        self.redraw()

    def delete_field_at(self, idx: int, confirm: bool = False) -> None:
        if not (0 <= idx < len(self.fields)):
            return
        self.editing_name_index = None
        field_name = self.fields[idx].name
        if confirm and not self.dialogs.askyesno(
            "項目削除",
            f"{field_name} を削除しますか。",
            yes_text="削除する",
            no_text="戻る",
            destructive=True,
        ):
            return
        self._set_undo(
            "delete",
            idx=idx,
            field=replace(self.fields[idx]),
            result=self.current_results[idx] if idx < len(self.current_results) else "",
            raw_result=(
                self.current_raw_results[idx]
                if idx < len(self.current_raw_results)
                else ""
            ),
        )
        del self.fields[idx]
        self._mark_dirty()
        if idx < len(self.current_results):
            del self.current_results[idx]
        if idx < len(self.current_raw_results):
            del self.current_raw_results[idx]
        self.selected_index = min(idx, len(self.fields) - 1) if self.fields else None
        self.status_var.set(f"{field_name} を削除しました。Esc / Ctrl+Z で戻せます。")
        self._render_side_body()
        self.redraw()

    def _require_field_selection(self) -> int | None:
        if self.selected_index is None or not (
            0 <= self.selected_index < len(self.fields)
        ):
            self.dialogs.showinfo("項目未選択", "読み取り項目を選択してください。")
            return None
        return self.selected_index

    def _set_undo(self, action_type: str, **payload) -> None:
        self.undo_action = {"type": action_type, **payload}

    def _focus_is_text_entry(self) -> bool:
        focused = self.root.focus_get()
        if focused is None:
            return False
        return "Entry" in str(focused.winfo_class())

    def undo_last_action(self, event=None) -> str | None:
        if (
            event is not None
            and getattr(event, "keysym", "").lower() == "z"
            and self._focus_is_text_entry()
        ):
            return None
        if self.mode_var.get() != MODE_TEMPLATE:
            return "break"
        if self.canvas_edit_state is not None or self.drag_start is not None:
            self.canvas_edit_state = None
            self.drag_start = None
            if self.drag_rect:
                self.canvas.delete(self.drag_rect)
                self.drag_rect = None
            self.status_var.set("プレビュー操作をキャンセルしました。")
            self.redraw()
            return "break"

        action = self.undo_action
        if not action:
            self.status_var.set("取り消せる直前操作がありません。")
            return "break"

        self.editing_name_index = None
        action_type = action.get("type")
        if action_type == "add":
            self._undo_added_field(action)
        elif action_type == "delete":
            self._undo_deleted_field(action)
        elif action_type == "update_rect":
            self._undo_updated_rect(action)
        elif action_type == "update_set_rects":
            self._undo_updated_set_rects(action)
        else:
            self.status_var.set("取り消せる直前操作がありません。")
            self.undo_action = None
            return "break"

        self.undo_action = None
        self._mark_dirty()
        self._render_side_body()
        self.redraw()
        return "break"

    def _undo_added_field(self, action: dict) -> None:
        idx = int(action["idx"])
        field = action["field"]
        field_ref = action.get("field_ref")
        remove_idx = (
            idx if 0 <= idx < len(self.fields) and self.fields[idx] == field else None
        )
        if field_ref is not None:
            for candidate_idx, candidate in enumerate(self.fields):
                if candidate is field_ref:
                    remove_idx = candidate_idx
                    break
        if remove_idx is None:
            for candidate_idx, candidate in enumerate(self.fields):
                if candidate == field:
                    remove_idx = candidate_idx
                    break
        if remove_idx is None:
            self.status_var.set(
                "追加した項目が見つからないため、取り消せませんでした。"
            )
            return
        removed_field = self.fields[remove_idx]
        field_name = removed_field.name
        del self.fields[remove_idx]
        if remove_idx < len(self.current_results):
            del self.current_results[remove_idx]
        if remove_idx < len(self.current_raw_results):
            del self.current_raw_results[remove_idx]
        self.selected_index = (
            min(remove_idx, len(self.fields) - 1) if self.fields else None
        )
        if (
            removed_field.set_id > 0
            and removed_field.slot_key in self.set_definition.slot_keys
        ):
            self.pending_set_id = removed_field.set_id
            self.pending_slot_key = removed_field.slot_key
        self.status_var.set(f"{field_name} の追加を取り消しました。")

    def _undo_deleted_field(self, action: dict) -> None:
        idx = max(0, min(int(action["idx"]), len(self.fields)))
        field = replace(action["field"])
        self.fields.insert(idx, field)
        self.current_results.insert(idx, str(action.get("result") or ""))
        self.current_raw_results.insert(idx, str(action.get("raw_result") or ""))
        self.selected_index = idx
        self.status_var.set(f"{field.name} の削除を取り消しました。")

    def _undo_updated_rect(self, action: dict) -> None:
        idx = int(action["idx"])
        if not (0 <= idx < len(self.fields)):
            self.status_var.set(
                "更新前の項目が見つからないため、取り消せませんでした。"
            )
            return
        field = action["field"]
        current = self.fields[idx]
        current.x1, current.y1, current.x2, current.y2 = (
            field.x1,
            field.y1,
            field.x2,
            field.y2,
        )
        current.source_width, current.source_height = (
            field.source_width,
            field.source_height,
        )
        self._ensure_current_results()
        self.current_results[idx] = str(action.get("result") or "")
        self.current_raw_results[idx] = str(action.get("raw_result") or "")
        self.selected_index = idx
        self.status_var.set(f"{current.name} の範囲変更を取り消しました。")

    def _undo_updated_set_rects(self, action: dict) -> None:
        fields: dict[int, TemplateField] = action.get("fields") or {}
        if not fields:
            self.status_var.set(
                "更新前のセットが見つからないため、取り消せませんでした。"
            )
            return
        restored_indexes: list[int] = []
        for idx, field in fields.items():
            if not (0 <= idx < len(self.fields)):
                continue
            current = self.fields[idx]
            current.x1, current.y1, current.x2, current.y2 = (
                field.x1,
                field.y1,
                field.x2,
                field.y2,
            )
            current.source_width, current.source_height = (
                field.source_width,
                field.source_height,
            )
            restored_indexes.append(idx)
        if not restored_indexes:
            self.status_var.set(
                "更新前のセットが見つからないため、取り消せませんでした。"
            )
            return
        self._ensure_current_results()
        results: dict[int, object] = action.get("results") or {}
        for idx, value in results.items():
            if 0 <= idx < len(self.current_results):
                self.current_results[idx] = str(value or "")
        raw_results: dict[int, object] = action.get("raw_results") or {}
        for idx, value in raw_results.items():
            if 0 <= idx < len(self.current_raw_results):
                self.current_raw_results[idx] = str(value or "")
        selected = action.get("selected")
        if selected is None:
            self.selected_index = None
        else:
            selected_idx = int(selected)
            self.selected_index = (
                selected_idx
                if 0 <= selected_idx < len(self.fields)
                else restored_indexes[0]
            )
        self.status_var.set("セットの移動を取り消しました。")

    def redraw(self) -> None:
        self.canvas.delete("all")
        self.canvas_rects.clear()
        self.canvas_labels.clear()
        self.canvas_label_bgs.clear()
        self.canvas_set_handles.clear()
        if not self.original_image:
            self._draw_canvas_empty()
            return
        w, h = self.original_image.size
        scaled_width = max(1, int(w * self.zoom))
        scaled_height = max(1, int(h * self.zoom))
        cache_key = (id(self.original_image), self.zoom)
        if self.preview_image is None or self.preview_cache_key != cache_key:
            scaled = self.original_image.resize(
                (scaled_width, scaled_height), Image.Resampling.LANCZOS
            )
            self.preview_image = ImageTk.PhotoImage(scaled)
            self.preview_cache_key = cache_key
        self.image_item = self.canvas.create_image(
            0, 0, anchor="nw", image=self.preview_image
        )
        self.canvas.configure(scrollregion=(0, 0, scaled_width, scaled_height))
        self._sync_canvas_scrollbars()
        for idx in self._canvas_draw_order():
            self._draw_field(idx)
        self._draw_set_handles()

    def _canvas_draw_order(self) -> list[int]:
        indexes = list(range(len(self.fields)))
        selected = self._active_canvas_selection()
        if selected is None or not (0 <= selected < len(self.fields)):
            return indexes
        return [idx for idx in indexes if idx != selected] + [selected]

    def _active_canvas_selection(self) -> int | None:
        selected = self.selected_index
        if selected is not None and 0 <= selected < len(self.fields):
            return selected
        return None

    def _set_field_indexes(self) -> list[tuple[int, tuple[int, ...]]]:
        if self.export_layout_var.get() != EXPORT_LAYOUT_SET:
            return []
        grouped: dict[int, dict[str, int]] = {}
        for idx, field in enumerate(self.fields):
            if (
                not field.enabled
                or field.set_id <= 0
                or field.slot_key not in self.set_definition.allowed_slot_keys()
            ):
                continue
            grouped.setdefault(field.set_id, {})[field.slot_key] = idx
        complete: list[tuple[int, tuple[int, ...]]] = []
        for set_id, roles in sorted(grouped.items()):
            if not all(key in roles for key in self.set_definition.slot_keys):
                continue
            indexes = [roles[key] for key in self.set_definition.slot_keys]
            indexes.extend(
                roles[column.key]
                for column in self.set_definition.extra_slots
                if column.key in roles
            )
            complete.append((set_id, tuple(indexes)))
        return complete

    def _set_for_field(self, idx: int) -> tuple[int, ...] | None:
        for _set_id, indexes in self._set_field_indexes():
            if idx in indexes:
                return indexes
        return None

    def _canvas_editing_index(self) -> int | None:
        if self.canvas_edit_state is None:
            return None
        idx = self.canvas_edit_state.get("idx")
        return int(idx) if idx is not None else None

    def _should_draw_field_rect(self, idx: int) -> bool:
        set_indexes = self._set_for_field(idx)
        if set_indexes is None:
            return True
        editing_idx = self._canvas_editing_index()
        if editing_idx is not None:
            return editing_idx == idx
        if (
            self.canvas_edit_state is not None
            and self.canvas_edit_state.get("mode") == "set_move"
        ):
            return False
        return self.selected_index == idx

    def _should_show_canvas_label(self, idx: int) -> bool:
        editing_idx = self._canvas_editing_index()
        if editing_idx is not None:
            return editing_idx == idx
        if not self._should_draw_field_rect(idx):
            return False
        selected = self._active_canvas_selection()
        return selected is None or selected == idx

    def _draw_canvas_empty(self) -> None:
        canvas_w = max(self.canvas.winfo_width(), 900)
        canvas_h = max(self.canvas.winfo_height(), 600)
        self.canvas.configure(scrollregion=(0, 0, canvas_w, canvas_h))
        self._set_canvas_scrollbars(False, False)
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.canvas.create_text(
            canvas_w / 2,
            canvas_h / 2 - 20,
            text=THEME.copy.empty_canvas_title,
            fill=THEME.palette.empty_title,
            font=UI_FONT_TITLE,
        )
        self.canvas.create_text(
            canvas_w / 2,
            canvas_h / 2 + 12,
            text=THEME.copy.empty_canvas_note,
            fill=THEME.palette.empty_note,
            font=UI_FONT_SMALL,
        )

    def _set_canvas_scrollbars(self, horizontal: bool, vertical: bool) -> None:
        if self.hbar is not None:
            if horizontal:
                self.hbar.grid(row=1, column=0, sticky="ew")
            else:
                self.hbar.grid_remove()
        if self.vbar is not None:
            if vertical:
                self.vbar.grid(row=0, column=1, sticky="ns")
            else:
                self.vbar.grid_remove()

    def _sync_canvas_scrollbars(self) -> None:
        if not self.original_image:
            self._set_canvas_scrollbars(False, False)
            return
        parts = [
            float(value) for value in str(self.canvas.cget("scrollregion")).split()
        ]
        if len(parts) != 4:
            self._set_canvas_scrollbars(False, False)
            return
        region_w = parts[2] - parts[0]
        region_h = parts[3] - parts[1]
        visible_w = max(1, self.canvas.winfo_width())
        visible_h = max(1, self.canvas.winfo_height())
        self._set_canvas_scrollbars(region_w > visible_w + 1, region_h > visible_h + 1)

    def on_canvas_configure(self, _event=None) -> None:
        self._sync_canvas_scrollbars()

    def _draw_field(self, idx: int) -> None:
        field = self._scaled_field(self.fields[idx])
        x1, y1, x2, y2 = [
            value * self.zoom for value in (field.x1, field.y1, field.x2, field.y2)
        ]
        selected = idx == self.selected_index
        selection_active = self._active_canvas_selection() is not None
        if not self._should_draw_field_rect(idx):
            return
        show_label = self._should_show_canvas_label(idx)
        line_mode = field.ocr_line_split == "detected"
        if field.enabled:
            if line_mode:
                color = (
                    THEME.palette.line_region_selected
                    if selected
                    else (
                        THEME.palette.inactive_line_region
                        if selection_active
                        else THEME.palette.line_region
                    )
                )
                label_bg = THEME.palette.line_badge
            else:
                color = (
                    COLOR_CTA
                    if selected
                    else (
                        THEME.palette.inactive_region
                        if selection_active
                        else THEME.palette.enabled_region
                    )
                )
                label_bg = COLOR_CTA_HOVER if selected else COLOR_PRIMARY
        else:
            color = THEME.palette.disabled_fill
            label_bg = COLOR_MUTED
        rect_width = 3 if selected else 1 if selection_active else 2
        if line_mode and field.enabled:
            rect = self.canvas.create_rectangle(
                x1, y1, x2, y2, outline=color, width=rect_width, dash=(6, 3)
            )
        else:
            rect = self.canvas.create_rectangle(
                x1, y1, x2, y2, outline=color, width=rect_width
            )
        if (
            line_mode
            and field.enabled
            and self.canvas_edit_state is None
            and (selected or not selection_active)
        ):
            self._draw_field_line_guides(field, selected)
        self.canvas_rects[idx] = rect
        if not show_label:
            return
        label_text = f"{self._field_order_label(idx)} {field.name}"
        if line_mode:
            label_text = f"{label_text} | 行"
        label = self.canvas.create_text(
            x1 + 10,
            max(6, y1 - 22),
            anchor="nw",
            text=label_text,
            fill=THEME.palette.on_color,
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
            self.canvas_label_bgs[idx] = bg
        self.canvas_labels[idx] = label

    def _draw_set_handles(self) -> None:
        if self.original_image is None:
            return
        for set_id, indexes in self._set_field_indexes():
            rects = {}
            for idx in indexes:
                field = self._scaled_field(self.fields[idx]).normalized()
                rects[idx] = (field.x1, field.y1, field.x2, field.y2)
            self._draw_set_handle(set_id, indexes, rects)

    def _draw_set_handle(
        self,
        set_position: int,
        indexes: tuple[int, ...],
        rects: dict[int, tuple[int, int, int, int]],
    ) -> None:
        x1, y1, x2, y2 = self._set_canvas_rect(rects)
        selected = self.selected_index in indexes
        outline = self.canvas.create_rectangle(
            x1,
            y1,
            x2,
            y2,
            outline=(
                THEME.palette.set_outline_selected
                if selected
                else THEME.palette.set_outline
            ),
            width=2 if selected else 1,
            dash=(5, 4),
        )
        if not selected:
            self.canvas_set_handles[set_position] = {
                "fields": indexes,
                "outline": outline,
                "handle": None,
                "label": None,
                "selected": False,
            }
            return
        handle_w, handle_h = 34, 22
        handle_x1 = max(2, x1 + 4)
        handle_y1 = max(2, y1 + 4)
        handle_x2 = handle_x1 + handle_w
        handle_y2 = handle_y1 + handle_h
        handle = self.canvas.create_rectangle(
            handle_x1,
            handle_y1,
            handle_x2,
            handle_y2,
            fill=THEME.palette.set_handle,
            outline=THEME.palette.set_outline_selected,
            width=1,
        )
        label = self.canvas.create_text(
            (handle_x1 + handle_x2) / 2,
            (handle_y1 + handle_y2) / 2,
            text=str(set_position),
            fill=THEME.palette.on_color,
            font=UI_FONT_CANVAS,
        )
        self.canvas_set_handles[set_position] = {
            "fields": indexes,
            "outline": outline,
            "handle": handle,
            "label": label,
            "selected": True,
        }

    def _set_union(
        self, rects: dict[int, tuple[int, int, int, int]]
    ) -> tuple[int, int, int, int]:
        return (
            min(rect[0] for rect in rects.values()),
            min(rect[1] for rect in rects.values()),
            max(rect[2] for rect in rects.values()),
            max(rect[3] for rect in rects.values()),
        )

    def _set_canvas_rect(
        self, rects: dict[int, tuple[int, int, int, int]]
    ) -> tuple[float, float, float, float]:
        padding = 7
        x1, y1, x2, y2 = [value * self.zoom for value in self._set_union(rects)]
        return max(0, x1 - padding), max(0, y1 - padding), x2 + padding, y2 + padding

    def _hit_test_set_handle(self, canvas_x: float, canvas_y: float) -> int | None:
        for set_position in reversed(list(self.canvas_set_handles.keys())):
            state = self.canvas_set_handles[set_position]
            item = state.get("handle") or state.get("outline")
            if item is None:
                continue
            bbox = self.canvas.bbox(item)
            if not bbox:
                continue
            left, top, right, bottom = bbox
            if state.get("handle") is not None:
                hit = (
                    left - 4 <= canvas_x <= right + 4
                    and top - 4 <= canvas_y <= bottom + 4
                )
            else:
                tolerance = 5
                inside_x = left - tolerance <= canvas_x <= right + tolerance
                inside_y = top - tolerance <= canvas_y <= bottom + tolerance
                near_vertical = (
                    abs(canvas_x - left) <= tolerance
                    or abs(canvas_x - right) <= tolerance
                )
                near_horizontal = (
                    abs(canvas_y - top) <= tolerance
                    or abs(canvas_y - bottom) <= tolerance
                )
                hit = (inside_y and near_vertical) or (inside_x and near_horizontal)
            if hit:
                return set_position
        return None

    def _set_handle_preview(
        self, set_position: int, rects: dict[int, tuple[int, int, int, int]]
    ) -> None:
        handle_state = self.canvas_set_handles.get(set_position)
        if not handle_state:
            return
        x1, y1, x2, y2 = self._set_canvas_rect(rects)
        outline = handle_state.get("outline")
        if outline is not None:
            self.canvas.coords(outline, x1, y1, x2, y2)
        handle = handle_state.get("handle")
        label = handle_state.get("label")
        if handle is None or label is None:
            return
        handle_w, handle_h = 34, 22
        handle_x1 = max(2, x1 + 4)
        handle_y1 = max(2, y1 + 4)
        handle_x2 = handle_x1 + handle_w
        handle_y2 = handle_y1 + handle_h
        self.canvas.coords(handle, handle_x1, handle_y1, handle_x2, handle_y2)
        self.canvas.coords(
            label, (handle_x1 + handle_x2) / 2, (handle_y1 + handle_y2) / 2
        )

    def _draw_field_line_guides(self, field: TemplateField, selected: bool) -> None:
        if self.original_image is None:
            return
        normalized = field.normalized()
        crop = self.original_image.crop(
            (normalized.x1, normalized.y1, normalized.x2, normalized.y2)
        )
        bands = detect_line_bands(
            crop,
            normalized.ocr_line_detect_threshold,
            normalized.ocr_line_detect_min_ratio,
            normalized.ocr_line_detect_gap,
            normalized.ocr_line_padding,
        )
        color = (
            THEME.palette.line_region_selected if selected else THEME.palette.line_guide
        )
        left = normalized.x1 * self.zoom
        right = normalized.x2 * self.zoom
        for band_y1, band_y2 in bands:
            top = (normalized.y1 + band_y1) * self.zoom
            bottom = (normalized.y1 + band_y2) * self.zoom
            self.canvas.create_line(
                left, top, right, top, fill=color, width=1, dash=(2, 4)
            )
            self.canvas.create_line(
                left, bottom, right, bottom, fill=color, width=1, dash=(2, 4)
            )

    def _event_canvas_point(self, event) -> tuple[float, float]:
        return (
            cast(float, self.canvas.canvasx(event.x)),
            cast(float, self.canvas.canvasy(event.y)),
        )

    def _event_image_point(self, event) -> tuple[int, int]:
        canvas_x, canvas_y = self._event_canvas_point(event)
        return int(canvas_x / self.zoom), int(canvas_y / self.zoom)

    def _hit_test_label(self, canvas_x: float, canvas_y: float) -> int | None:
        for idx in reversed(self._canvas_draw_order()):
            item = self.canvas_label_bgs.get(idx) or self.canvas_labels.get(idx)
            if item is None:
                continue
            bbox = self.canvas.bbox(item)
            if not bbox:
                continue
            left, top, right, bottom = bbox
            if left - 4 <= canvas_x <= right + 4 and top - 4 <= canvas_y <= bottom + 4:
                return idx
        return None

    def _hit_test_field(self, x: int, y: int) -> tuple[int | None, str | None]:
        tolerance = max(6.0, 10.0 / max(self.zoom, 0.1))
        for idx in reversed(self._canvas_draw_order()):
            field = self._scaled_field(self.fields[idx]).normalized()
            x1, y1, x2, y2 = field.x1, field.y1, field.x2, field.y2
            width = max(1, x2 - x1)
            height = max(1, y2 - y1)
            resize_x = min(tolerance, max(4.0, width * 0.22))
            resize_y = min(tolerance, max(4.0, height * 0.22))
            inside_x = x1 - tolerance <= x <= x2 + tolerance
            inside_y = y1 - tolerance <= y <= y2 + tolerance

            if x1 <= x <= x2 and y1 <= y <= y2:
                center_x = x1 + resize_x < x < x2 - resize_x
                center_y = y1 + resize_y < y < y2 - resize_y
                if center_x and center_y:
                    return idx, "move"

            near_left = abs(x - x1) <= resize_x
            near_right = abs(x - x2) <= resize_x
            near_top = abs(y - y1) <= resize_y
            near_bottom = abs(y - y2) <= resize_y

            if near_left and near_top:
                return idx, "nw"
            if near_right and near_top:
                return idx, "ne"
            if near_left and near_bottom:
                return idx, "sw"
            if near_right and near_bottom:
                return idx, "se"
            if near_left and inside_y:
                return idx, "w"
            if near_right and inside_y:
                return idx, "e"
            if near_top and inside_x:
                return idx, "n"
            if near_bottom and inside_x:
                return idx, "s"
            if x1 <= x <= x2 and y1 <= y <= y2:
                return idx, "move"
        return None, None

    def _cursor_for_edit_mode(self, mode: str | None) -> str:
        if mode in {"move", "set_move"}:
            return "fleur"
        if mode in {"e", "w"}:
            return "sb_h_double_arrow"
        if mode in {"n", "s"}:
            return "sb_v_double_arrow"
        if mode:
            return "crosshair"
        return "arrow"

    def on_canvas_motion(self, event) -> None:
        if not self.original_image:
            self.canvas.configure(cursor="arrow")
            return
        mode = self.mode_var.get()
        if mode == MODE_EXPORT:
            self.canvas.configure(cursor="arrow")
            return
        if mode == MODE_REVIEW:
            self.canvas.configure(
                cursor=(
                    "hand2"
                    if self._canvas_selection_hit(event) is not None
                    else "arrow"
                )
            )
            return
        if self.canvas_edit_state is not None:
            self.canvas.configure(
                cursor=self._cursor_for_edit_mode(
                    str(self.canvas_edit_state.get("mode"))
                )
            )
            return
        if self.drag_start is not None:
            self.canvas.configure(cursor="crosshair")
            return
        canvas_x, canvas_y = self._event_canvas_point(event)
        set_hit = self._hit_test_set_handle(canvas_x, canvas_y)
        if set_hit is not None:
            state = self.canvas_set_handles.get(set_hit) or {}
            self.canvas.configure(cursor="fleur" if state.get("selected") else "hand2")
            return
        x, y = self._event_image_point(event)
        _idx, mode = self._hit_test_field(x, y)
        if mode and mode != "move":
            self.canvas.configure(cursor=self._cursor_for_edit_mode(mode))
            return
        label_hit_idx = self._hit_test_label(canvas_x, canvas_y)
        if label_hit_idx is not None:
            self.canvas.configure(cursor="fleur")
            return
        self.canvas.configure(cursor=self._cursor_for_edit_mode(mode))

    def _edited_rect_from_state(self, x: int, y: int) -> tuple[int, int, int, int]:
        assert self.canvas_edit_state is not None
        mode = str(self.canvas_edit_state["mode"])
        start_x = int(self.canvas_edit_state["start_x"])
        start_y = int(self.canvas_edit_state["start_y"])
        x1, y1, x2, y2 = self.canvas_edit_state["orig"]
        dx = x - start_x
        dy = y - start_y

        if mode == "move":
            return int(x1 + dx), int(y1 + dy), int(x2 + dx), int(y2 + dy)
        if "w" in mode:
            x1 += dx
        if "e" in mode:
            x2 += dx
        if "n" in mode:
            y1 += dy
        if "s" in mode:
            y2 += dy
        return int(x1), int(y1), int(x2), int(y2)

    def _clamp_rect_to_image(
        self, rect: tuple[int, int, int, int]
    ) -> tuple[int, int, int, int]:
        assert self.original_image is not None
        w, h = self.original_image.size
        x1, y1, x2, y2 = rect
        if self.canvas_edit_state and self.canvas_edit_state.get("mode") == "move":
            width = x2 - x1
            height = y2 - y1
            x1 = max(0, min(w - width, x1))
            y1 = max(0, min(h - height, y1))
            return x1, y1, x1 + width, y1 + height

        x1, x2 = sorted((max(0, min(w, x1)), max(0, min(w, x2))))
        y1, y2 = sorted((max(0, min(h, y1)), max(0, min(h, y2))))
        return x1, y1, x2, y2

    def _set_move_rects_from_state(
        self, x: int, y: int
    ) -> dict[int, tuple[int, int, int, int]]:
        assert self.canvas_edit_state is not None
        assert self.original_image is not None
        start_x = int(self.canvas_edit_state["start_x"])
        start_y = int(self.canvas_edit_state["start_y"])
        origs: dict[int, tuple[int, int, int, int]] = self.canvas_edit_state["origs"]
        dx = x - start_x
        dy = y - start_y
        image_w, image_h = self.original_image.size
        min_left = min(rect[0] for rect in origs.values())
        min_top = min(rect[1] for rect in origs.values())
        max_right = max(rect[2] for rect in origs.values())
        max_bottom = max(rect[3] for rect in origs.values())
        dx = max(-min_left, min(image_w - max_right, dx))
        dy = max(-min_top, min(image_h - max_bottom, dy))
        return {
            idx: (int(x1 + dx), int(y1 + dy), int(x2 + dx), int(y2 + dy))
            for idx, (x1, y1, x2, y2) in origs.items()
        }

    def _set_canvas_edit_preview(
        self, idx: int, rect: tuple[int, int, int, int]
    ) -> None:
        item = self.canvas_rects.get(idx)
        if item is None:
            return
        x1, y1, x2, y2 = rect
        self.canvas.coords(
            item, x1 * self.zoom, y1 * self.zoom, x2 * self.zoom, y2 * self.zoom
        )
        label = self.canvas_labels.get(idx)
        if label is None:
            return
        label_x = x1 * self.zoom + 10
        label_y = max(6, y1 * self.zoom - 22)
        self.canvas.coords(label, label_x, label_y)
        label_box = self.canvas.bbox(label)
        label_bg = self.canvas_label_bgs.get(idx)
        if label_bg is not None and label_box:
            self.canvas.coords(
                label_bg,
                label_box[0] - 5,
                label_box[1] - 3,
                label_box[2] + 5,
                label_box[3] + 3,
            )

    def on_canvas_double_click(self, event) -> str:
        if not self.original_image or self.mode_var.get() != MODE_TEMPLATE:
            return "break"
        canvas_x, canvas_y = self._event_canvas_point(event)
        idx = self._hit_test_label(canvas_x, canvas_y)
        if idx is None:
            x, y = self._event_image_point(event)
            idx, _mode = self._hit_test_field(x, y)
        if idx is None:
            return "break"
        self.canvas_edit_state = None
        self.drag_start = None
        if self.drag_rect:
            self.canvas.delete(self.drag_rect)
            self.drag_rect = None
        self.begin_inline_name_edit(idx)
        return "break"

    def on_mouse_down(self, event) -> None:
        if not self.original_image:
            return
        mode = self.mode_var.get()
        if mode == MODE_EXPORT:
            return
        if mode == MODE_REVIEW:
            hit_idx = self._canvas_selection_hit(event)
            if hit_idx is not None:
                self._select_field_index(hit_idx, update_review_detail=True)
            return
        if not self._commit_active_name_edit():
            return
        canvas_x, canvas_y = self._event_canvas_point(event)
        x, y = self._event_image_point(event)
        field_hit_idx, field_mode = self._hit_test_field(x, y)
        label_hit_idx = self._hit_test_label(canvas_x, canvas_y)
        set_hit = self._hit_test_set_handle(canvas_x, canvas_y)
        if set_hit is not None:
            self._adopt_current_image_as_coordinate_source()
            handle_state = self.canvas_set_handles.get(set_hit) or {}
            raw_indexes = handle_state.get("fields")
            indexes = (
                tuple(raw_indexes)
                if isinstance(raw_indexes, (list, tuple))
                else ()
            )
            if len(indexes) == 2:
                if not handle_state.get("selected"):
                    self._select_field_index(indexes[0], update_review_detail=True)
                    return
                origs = {}
                for idx in indexes:
                    field = self._scaled_field(self.fields[idx]).normalized()
                    origs[idx] = (field.x1, field.y1, field.x2, field.y2)
                self.canvas_edit_state = {
                    "set_position": set_hit,
                    "indexes": indexes,
                    "mode": "set_move",
                    "start_x": x,
                    "start_y": y,
                    "origs": origs,
                    "selected": self.selected_index,
                }
                self.status_var.set(f"セット #{set_hit} を移動中です。")
                self.redraw()
                return
        if field_hit_idx is not None and field_mode and field_mode != "move":
            hit_idx, mode = field_hit_idx, field_mode
        elif label_hit_idx is not None:
            hit_idx, mode = label_hit_idx, "move"
        else:
            hit_idx, mode = field_hit_idx, field_mode
        if hit_idx is not None and mode is not None:
            self._adopt_current_image_as_coordinate_source()
            field = self._scaled_field(self.fields[hit_idx]).normalized()
            self._select_field_index(
                hit_idx, update_review_detail=True, redraw_canvas=False
            )
            self.canvas_edit_state = {
                "idx": hit_idx,
                "mode": mode,
                "start_x": x,
                "start_y": y,
                "orig": (field.x1, field.y1, field.x2, field.y2),
            }
            action_text = "移動中" if mode == "move" else "サイズ調整中"
            self.status_var.set(
                f"{self.fields[hit_idx].name} の範囲を{action_text}です。"
            )
            self.redraw()
            return

        if self.selected_index is not None:
            self.selected_index = None
            self._schedule_side_body_render()
            self.redraw()

        self._adopt_current_image_as_coordinate_source()
        self.drag_start = (x, y)
        x = self.drag_start[0] * self.zoom
        y = self.drag_start[1] * self.zoom
        self.drag_rect = self.canvas.create_rectangle(
            x, y, x, y, outline=COLOR_CTA, width=2, dash=(4, 2)
        )

    def on_mouse_drag(self, event) -> None:
        if self.mode_var.get() != MODE_TEMPLATE:
            return
        if self.canvas_edit_state is not None:
            if self.canvas_edit_state.get("mode") == "set_move":
                x, y = self._event_image_point(event)
                rects = self._set_move_rects_from_state(x, y)
                for idx, rect in rects.items():
                    self._set_canvas_edit_preview(idx, rect)
                self._set_handle_preview(
                    int(self.canvas_edit_state["set_position"]), rects
                )
                return
            idx = int(self.canvas_edit_state["idx"])
            x, y = self._event_image_point(event)
            rect = self._clamp_rect_to_image(self._edited_rect_from_state(x, y))
            self._set_canvas_edit_preview(idx, rect)
            return
        if not self.drag_start or not self.drag_rect:
            return
        x1, y1 = self.drag_start
        x2, y2 = self._event_image_point(event)
        self.canvas.coords(
            self.drag_rect,
            x1 * self.zoom,
            y1 * self.zoom,
            x2 * self.zoom,
            y2 * self.zoom,
        )

    def on_mouse_up(self, event) -> None:
        if self.mode_var.get() != MODE_TEMPLATE:
            self._clear_canvas_pointer_interaction()
            return
        image = self.original_image
        if image is None:
            self._clear_canvas_pointer_interaction()
            return
        if self.canvas_edit_state is not None:
            if self.canvas_edit_state.get("mode") == "set_move":
                x, y = self._event_image_point(event)
                rects = self._set_move_rects_from_state(x, y)
                indexes = tuple(self.canvas_edit_state["indexes"])
                set_position = int(self.canvas_edit_state["set_position"])
                origs: dict[int, tuple[int, int, int, int]] = self.canvas_edit_state[
                    "origs"
                ]
                previous_selected = self.canvas_edit_state.get("selected")
                self.canvas_edit_state = None
                if not any(rects.get(idx) != origs.get(idx) for idx in indexes):
                    self.redraw()
                    return
                before_fields = {
                    idx: replace(self.fields[idx])
                    for idx in indexes
                    if 0 <= idx < len(self.fields)
                }
                before_results = {
                    idx: (
                        self.current_results[idx]
                        if idx < len(self.current_results)
                        else ""
                    )
                    for idx in indexes
                }
                before_raw_results = {
                    idx: (
                        self.current_raw_results[idx]
                        if idx < len(self.current_raw_results)
                        else ""
                    )
                    for idx in indexes
                }
                for idx, rect in rects.items():
                    if not (0 <= idx < len(self.fields)):
                        continue
                    field = self.fields[idx]
                    field.x1, field.y1, field.x2, field.y2 = rect
                    field.source_width, field.source_height = image.size
                    if idx < len(self.current_results):
                        self.current_results[idx] = ""
                    if idx < len(self.current_raw_results):
                        self.current_raw_results[idx] = ""
                self.selected_index = previous_selected
                self._set_undo(
                    "update_set_rects",
                    fields=before_fields,
                    results=before_results,
                    raw_results=before_raw_results,
                    selected=self.selected_index,
                )
                self._mark_dirty()
                self.status_var.set(
                    f"セット {set_position} の範囲を移動しました。Esc / Ctrl+Z で戻せます。"
                )
                self._schedule_side_body_render()
                self.redraw()
                return
            idx = int(self.canvas_edit_state["idx"])
            x, y = self._event_image_point(event)
            x1, y1, x2, y2 = self._clamp_rect_to_image(
                self._edited_rect_from_state(x, y)
            )
            self.canvas_edit_state = None
            if (x2 - x1) < 8 or (y2 - y1) < 8:
                self.redraw()
                return
            field = self.fields[idx]
            before_field = replace(field)
            before_result = (
                self.current_results[idx] if idx < len(self.current_results) else ""
            )
            before_raw_result = (
                self.current_raw_results[idx]
                if idx < len(self.current_raw_results)
                else ""
            )
            field.x1, field.y1, field.x2, field.y2 = x1, y1, x2, y2
            field.source_width, field.source_height = image.size
            if idx < len(self.current_results):
                self.current_results[idx] = ""
            if idx < len(self.current_raw_results):
                self.current_raw_results[idx] = ""
            self.selected_index = idx
            self._set_undo(
                "update_rect",
                idx=idx,
                field=before_field,
                result=before_result,
                raw_result=before_raw_result,
            )
            self._mark_dirty()
            self.status_var.set(
                f"{field.name} の範囲を更新しました。Esc / Ctrl+Z で戻せます。"
            )
            self._schedule_side_body_render()
            self.redraw()
            return

        if not self.original_image or not self.drag_start:
            return
        x1, y1 = self.drag_start
        x2, y2 = self._event_image_point(event)
        self.drag_start = None
        if self.drag_rect:
            self.canvas.delete(self.drag_rect)
            self.drag_rect = None

        w, h = self.original_image.size
        x1, x2 = sorted((max(0, min(w, x1)), max(0, min(w, x2))))
        y1, y2 = sorted((max(0, min(h, y1)), max(0, min(h, y2))))
        if (x2 - x1) < 8 or (y2 - y1) < 8:
            return

        set_id = (
            self.pending_set_id
            if self.export_layout_var.get() == EXPORT_LAYOUT_SET
            else None
        )
        slot_key = self.pending_slot_key if set_id is not None else ""
        if set_id is not None:
            slot_label = self.set_definition.slot_label(slot_key)
            name = self._unique_field_name(f"{slot_label}{set_id}")
        else:
            name = self._next_field_name()
        source_width, source_height = self.original_image.size
        field = TemplateField(
            name,
            x1,
            y1,
            x2,
            y2,
            True,
            source_width,
            source_height,
            set_id=set_id or 0,
            slot_key=slot_key,
        )
        if self.profile.has_field_ocr_preset(name):
            for key, value in self.profile.field_ocr_preset(name).items():
                setattr(field, key, value)
        slot_column = self.set_definition.column_for(slot_key)
        if slot_column is not None:
            field.ocr_line_split = slot_column.ocr_line_split
        self.fields.append(field)
        if set_id is not None:
            self.empty_set_ids.discard(set_id)
        self.current_results.append("")
        self.current_raw_results.append("")
        self.selected_index = len(self.fields) - 1
        self.mode_var.set(MODE_TEMPLATE)
        self._set_undo(
            "add",
            idx=self.selected_index,
            field=replace(self.fields[self.selected_index]),
            field_ref=self.fields[self.selected_index],
            result="",
        )
        self._mark_dirty()
        if set_id is not None:
            slot_keys = list(self.set_definition.slot_keys)
            current_position = slot_keys.index(slot_key)
            has_next = current_position + 1 < len(slot_keys)
        else:
            has_next = False
        if set_id is not None and has_next:
            self.pending_slot_key = slot_keys[current_position + 1]
            next_label = self.set_definition.slot_label(self.pending_slot_key)
            self.status_var.set(
                f"{name} を追加しました。続けてセット {set_id} の{next_label}範囲をドラッグしてください。"
            )
        elif set_id is not None:
            self.pending_set_id = None
            self.pending_slot_key = ""
            self.status_var.set(
                f"セット {set_id} を追加しました。Esc / Ctrl+Z で戻せます。"
            )
        else:
            self.editing_name_index = self.selected_index
            self.editing_name_value = name
            self.editing_name_error = ""
            self.status_var.set(
                "項目名を入力してください。Enterで確定、Escで元の名前に戻します。"
            )
        self._schedule_side_body_render()
        self.redraw()

    def _canvas_selection_hit(self, event) -> int | None:
        canvas_x, canvas_y = self._event_canvas_point(event)
        set_hit = self._hit_test_set_handle(canvas_x, canvas_y)
        if set_hit is not None:
            indexes = tuple(
                (self.canvas_set_handles.get(set_hit) or {}).get("fields") or ()
            )
            if indexes:
                return int(indexes[0])
        label_hit_idx = self._hit_test_label(canvas_x, canvas_y)
        if label_hit_idx is not None:
            return label_hit_idx
        x, y = self._event_image_point(event)
        field_hit_idx, _mode = self._hit_test_field(x, y)
        return field_hit_idx

    def _clear_canvas_pointer_interaction(self) -> None:
        self.canvas_edit_state = None
        self.drag_start = None
        if self.drag_rect:
            self.canvas.delete(self.drag_rect)
            self.drag_rect = None

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

    def ocr_selected_field(self) -> None:
        idx = self._require_field_selection()
        if idx is None:
            return
        if not self.fields[idx].enabled:
            self.dialogs.showinfo("項目無効", "選択中の読み取り項目が無効です。")
            return
        self.ocr_field_at(idx)

    def ocr_current_image(self) -> None:
        if not self.original_image:
            self.dialogs.showerror(
                "画像未選択", "先に画像フォルダを選択してください。"
            )
            return
        if not self._enabled_fields():
            self.dialogs.showerror("項目なし", "有効な読み取り項目がありません。")
            return
        if not self._require_ocr_ready(self.ocr_current_image):
            return
        image = self.original_image.copy()
        fields = [
            (idx, self._field_for_ocr(field))
            for idx, field in enumerate(self.fields)
        ]
        lang = self.lang_var.get()
        backend_config = ""
        backend = self.profile.default_backend
        correction_rules = [replace(rule) for rule in self.correction_rules]

        def work() -> tuple[list[str], list[str]]:
            raw_results = [""] * len(fields)
            results = [""] * len(fields)
            for idx, field in fields:
                self._raise_if_operation_cancelled()
                if not field.enabled:
                    continue
                raw_text, text, error = self.ocr_engine.recognize_field_detail(
                    image,
                    field,
                    lang,
                    backend_config,
                    backend,
                    correction_rules,
                    self.coordinate_settings,
                    self.text_formatting,
                )
                if error or text is None:
                    raise RuntimeError(error or f"{field.name} のOCRに失敗しました。")
                self._raise_if_operation_cancelled()
                raw_results[idx] = raw_text or ""
                results[idx] = text
            return raw_results, results

        def on_success(result: tuple[list[str], list[str]]) -> None:
            self.current_raw_results, self.current_results = result
            self.status_var.set("全項目をテストしました。")
            self._render_side_body()

        def on_error(error: Exception) -> None:
            self.status_var.set("OCRに失敗しました。")
            self.dialogs.showerror("OCRエラー", str(error))

        self._run_background(
            "認識テスト中です。初回は認識モデルの読み込みに時間がかかります。",
            work,
            on_success,
            on_error,
        )

    def ocr_field_at(self, idx: int) -> None:
        if not self.original_image:
            self.dialogs.showerror(
                "画像未選択", "先に画像フォルダを選択してください。"
            )
            return
        if not (0 <= idx < len(self.fields)) or not self.fields[idx].enabled:
            return
        if not self._require_ocr_ready(
            lambda field_index=idx: self.ocr_field_at(field_index)
        ):
            return
        image = self.original_image.copy()
        field = self._field_for_ocr(self.fields[idx])
        lang = self.lang_var.get()
        backend_config = ""
        backend = self.profile.default_backend
        correction_rules = [replace(rule) for rule in self.correction_rules]

        def work() -> tuple[int, str, str]:
            self._raise_if_operation_cancelled()
            raw_text, text, error = self.ocr_engine.recognize_field_detail(
                image,
                field,
                lang,
                backend_config,
                backend,
                correction_rules,
                self.coordinate_settings,
                self.text_formatting,
            )
            if error or text is None:
                raise RuntimeError(error or f"{field.name} のOCRに失敗しました。")
            self._raise_if_operation_cancelled()
            return idx, raw_text or "", text

        def on_success(result: tuple[int, str, str]) -> None:
            result_idx, raw_text, text = result
            if not (0 <= result_idx < len(self.fields)):
                return
            self._ensure_result_buffers()
            self.current_raw_results[result_idx] = raw_text
            self.current_results[result_idx] = text
            self.selected_index = result_idx
            self.status_var.set(
                f"{self.fields[result_idx].name} を認識テストしました。"
            )
            self._render_side_body()

        def on_error(error: Exception) -> None:
            self.status_var.set("OCRに失敗しました。")
            self.dialogs.showerror("OCRエラー", str(error))

        self._run_background(
            f"{field.name} を認識テスト中です。", work, on_success, on_error
        )

    def _ocr_all_current(self, show_errors: bool) -> bool:
        if not self.original_image:
            return False
        self._ensure_result_buffers()
        for idx, field in enumerate(self.fields):
            if not field.enabled:
                continue
            raw_text, text = self._ocr_field_detail(
                self.original_image, field, show_errors=show_errors
            )
            if text is None:
                return False
            self.current_raw_results[idx] = raw_text or ""
            self.current_results[idx] = text
        return True

    def _ocr_field_detail(
        self, image: Image.Image, field: TemplateField, show_errors: bool
    ) -> tuple[str | None, str | None]:
        raw_text, text, error = self.ocr_engine.recognize_field_detail(
            image,
            self._field_for_ocr(field),
            self.lang_var.get(),
            "",
            self.profile.default_backend,
            self.correction_rules,
            self.coordinate_settings,
            self.text_formatting,
        )
        if error and show_errors:
            self.dialogs.showerror("OCRエラー", error)
        return raw_text, text

    def _export_settings_dict(self) -> dict:
        return {
            "sheet_name": self.export_sheet_var.get(),
            "write_mode": self.export_write_mode_var.get(),
            "start_cell": self.export_start_cell_var.get(),
            "include_filename": self.export_include_filename_var.get(),
            "include_header": self.export_include_header_var.get(),
            "output_layout": self.export_layout_var.get(),
        }

    def _apply_output_settings(self, settings: dict) -> None:
        self.export_sheet_var.set(str(settings.get("sheet_name") or "OCR"))
        write_mode = str(settings.get("write_mode") or "上書き")
        self.export_write_mode_var.set(
            write_mode if write_mode in {"上書き", "追記"} else "上書き"
        )
        self.export_start_cell_var.set(str(settings.get("start_cell") or "A1"))
        self.export_include_filename_var.set(
            bool(settings.get("include_filename", True))
        )
        self.export_include_header_var.set(bool(settings.get("include_header", True)))
        output_layout = str(settings.get("output_layout") or EXPORT_LAYOUT_IMAGE_ROW)
        self.export_layout_var.set(
            output_layout
            if output_layout in EXPORT_LAYOUT_OPTIONS
            else EXPORT_LAYOUT_IMAGE_ROW
        )

    def _build_export_settings(self) -> tuple[ExportSettings | None, str | None]:
        return validate_export_settings(
            self.export_sheet_var.get(),
            self.export_write_mode_var.get(),
            self.export_start_cell_var.get(),
            self.export_include_filename_var.get(),
            self.export_include_header_var.get(),
            self.export_layout_var.get(),
        )

    def _build_csv_export_settings(self) -> ExportSettings:
        return ExportSettings(
            sheet_name="CSV",
            write_mode="上書き",
            start_row=1,
            start_col=1,
            include_filename=self.export_include_filename_var.get(),
            include_header=self.export_include_header_var.get(),
            output_layout=self.export_layout_var.get(),
        )

    def export_to_csv(self, _event=None) -> None:
        fields = self._enabled_fields()
        if not fields:
            self.dialogs.showerror("項目なし", "有効な読み取り項目がありません。")
            return
        set_error = self._validate_set_export(fields)
        if set_error:
            self.dialogs.showerror("セット設定エラー", set_error)
            return
        if not self.image_files:
            self.dialogs.showerror("画像なし", "画像フォルダを選択してください。")
            return
        image_files = self.image_queue.included_files()
        if not image_files:
            self.dialogs.showerror(
                "処理対象なし", "画像キューで処理する画像を1枚以上選択してください。"
            )
            return
        if not self._require_ocr_ready(self.export_to_csv):
            return

        file_name = filedialog.asksaveasfilename(
            title="CSV出力先を選択",
            defaultextension=".csv",
            filetypes=[("CSV file", "*.csv")],
            confirmoverwrite=False,
        )
        if not file_name:
            return
        output_path = Path(file_name)
        if output_path.exists() and not self.dialogs.askyesno(
            "上書き確認",
            f"既存ファイルを上書きします。\n{output_path}",
            yes_text="上書きする",
            no_text="戻る",
            destructive=True,
        ):
            return

        settings = self._build_csv_export_settings()
        total = len(image_files)
        self.progress_var.set(f"0 / {total}")
        self.status_var.set("CSV出力を開始しました。")
        image_files = list(image_files)
        export_fields = [self._field_for_ocr(field) for field in fields]
        coordinate_settings = replace(self.coordinate_settings)
        lang = self.lang_var.get()
        backend_config = ""
        backend = self.profile.default_backend
        correction_rules = [replace(rule) for rule in self.correction_rules]
        auto_detection = self.profile.auto_detection
        text_formatting = self.text_formatting
        export_definition = self.set_definition
        set_resolver = None
        if (
            auto_detection is not None
            and settings.output_layout == EXPORT_LAYOUT_SET
            and self.auto_detection_enabled
            and export_definition.preset == self.profile.default_set_preset
        ):
            set_resolver = lambda image, detection_fields: cast(
                SetDetectionResult,
                auto_detection.resolve_sets(
                    image,
                    detection_fields,
                    coordinate_settings,
                ),
            )

        export_signature = self._export_structure_signature(export_fields, settings)
        self._clear_retry_context()
        self.image_queue.prepare(image_files)
        for path in image_files:
            self._refresh_queue_item_status(path)

        def update_progress(
            row_index: int, total_count: int, image_path: Path
        ) -> None:
            def apply_update() -> None:
                self.image_queue.mark_processing(image_path)
                self._refresh_queue_item_status(image_path)
                self.progress_var.set(f"{row_index} / {total_count}")
                self.status_var.set(f"OCR中: {image_path.name}")
                self._update_loading(
                    "CSVへ出力しています",
                    note=f"{row_index} / {total_count} 画像\n{image_path.name}",
                    progress=row_index / max(1, total_count),
                )

            self.root.after(0, apply_update)

        def work():
            return self.csv_exporter.export(
                output_path,
                image_files,
                export_fields,
                settings,
                lambda image, field: self.ocr_engine.recognize_field(
                    image,
                    field,
                    lang,
                    backend_config,
                    backend,
                    correction_rules,
                    coordinate_settings,
                    text_formatting,
                ),
                update_progress,
                set_definition=export_definition,
                set_resolver=set_resolver,
                cancel_check=self._is_operation_cancelled,
            )

        def on_success(result: ExportResult) -> None:
            logger.info(
                "CSV export result | images=%d | errors=%d | notices=%d",
                result.total_images,
                len(result.errors),
                len(result.notices),
            )
            self.last_export_result = result
            self.last_export_signature = export_signature
            self.last_export_output_path = output_path
            self.last_export_format = "csv"
            self.image_queue.apply_result(result)
            self._render_side_body()
            if result.errors:
                self.status_var.set(
                    f"CSVへ出力しました（エラー {len(result.errors)} 件）: {output_path}"
                )
                self.dialogs.showwarning(
                    "完了（エラーあり）",
                    f"{result.total_images} 画像の処理が完了しました。"
                    f"\nOCRエラー {len(result.errors)} 件はCSVで空欄になっています。"
                    "\n該当画像は画像キューで確認できます。"
                    + (
                        f"\n除外通知: {len(result.notices)} 件"
                        if result.notices
                        else ""
                    )
                    + f"\n{output_path}",
                )
            else:
                notice_suffix = (
                    f"（除外通知 {len(result.notices)} 件）"
                    if result.notices
                    else ""
                )
                self.status_var.set(f"CSVへ出力しました{notice_suffix}: {output_path}")
                self.dialogs.showinfo(
                    "完了",
                    f"{result.total_images} 画像をCSVへ出力しました。"
                    + (
                        f"\n除外通知: {len(result.notices)} 件"
                        if result.notices
                        else ""
                    )
                    + f"\n{output_path}",
                )

        def on_error(error: Exception) -> None:
            self.image_queue.prepare(image_files)
            self._render_side_body()
            self.status_var.set("CSV出力に失敗しました。")
            self.dialogs.showerror(
                "出力エラー", f"CSVへ出力できませんでした。\n{error}"
            )

        def on_finally() -> None:
            self.progress_var.set("")

        def on_cancel() -> None:
            self.image_queue.prepare(image_files)
            self._render_side_body()
            self.status_var.set(
                "CSV出力をキャンセルしました。ファイルは変更されていません。"
            )

        self._run_background(
            "CSVへ出力しています",
            work,
            on_success,
            on_error,
            on_finally,
            on_cancel,
            loading_note=f"0 / {total} 画像\n出力の準備をしています。",
            loading_progress=0.0,
        )

    def export_to_excel(self, _event=None) -> None:
        fields = self._enabled_fields()
        if not fields:
            self.dialogs.showerror("項目なし", "有効な読み取り項目がありません。")
            return
        set_error = self._validate_set_export(fields)
        if set_error:
            self.dialogs.showerror("セット設定エラー", set_error)
            return
        if not self.image_files:
            self.dialogs.showerror("画像なし", "画像フォルダを選択してください。")
            return
        image_files = self.image_queue.included_files()
        if not image_files:
            self.dialogs.showerror(
                "処理対象なし", "画像キューで処理する画像を1枚以上選択してください。"
            )
            return
        if not self._require_ocr_ready(self.export_to_excel):
            return
        if not self.output_path:
            self.select_output_file()
            if not self.output_path:
                return
        settings, error = self._build_export_settings()
        if error or settings is None:
            self.dialogs.showerror(
                "出力設定エラー", error or "Excel出力設定を確認してください。"
            )
            return
        if (
            settings.write_mode == "上書き"
            and self.output_path.exists()
            and not self.dialogs.askyesno(
                "上書き確認",
                f"既存ファイルを上書きします。\n{self.output_path}",
                yes_text="上書きする",
                no_text="戻る",
                destructive=True,
            )
        ):
            return

        total = len(image_files)
        self.progress_var.set(f"0 / {total}")
        self.status_var.set("Excel出力を開始しました。")
        output_path = self.output_path
        image_files = list(image_files)
        export_fields = [self._field_for_ocr(field) for field in fields]
        coordinate_settings = replace(self.coordinate_settings)
        lang = self.lang_var.get()
        backend_config = ""
        backend = self.profile.default_backend
        correction_rules = [replace(rule) for rule in self.correction_rules]
        auto_detection = self.profile.auto_detection
        text_formatting = self.text_formatting
        export_definition = self.set_definition
        set_resolver = None
        if (
            auto_detection is not None
            and settings.output_layout == EXPORT_LAYOUT_SET
            and self.auto_detection_enabled
            and export_definition.preset == self.profile.default_set_preset
        ):
            set_resolver = lambda image, detection_fields: cast(
                SetDetectionResult,
                auto_detection.resolve_sets(
                    image,
                    detection_fields,
                    coordinate_settings,
                ),
            )
        export_signature = self._export_structure_signature(export_fields, settings)
        self._clear_retry_context()
        self.image_queue.prepare(image_files)
        for path in image_files:
            self._refresh_queue_item_status(path)

        def update_progress(row_index: int, total_count: int, image_path: Path) -> None:
            def apply_update() -> None:
                self.image_queue.mark_processing(image_path)
                self._refresh_queue_item_status(image_path)
                self.progress_var.set(f"{row_index} / {total_count}")
                self.status_var.set(f"OCR中: {image_path.name}")
                self._update_loading(
                    "Excelへ出力しています",
                    note=f"{row_index} / {total_count} 画像\n{image_path.name}",
                    progress=row_index / max(1, total_count),
                )

            self.root.after(0, apply_update)

        def work():
            return self.excel_exporter.export(
                output_path,
                image_files,
                export_fields,
                settings,
                lambda image, field: self.ocr_engine.recognize_field(
                    image,
                    field,
                    lang,
                    backend_config,
                    backend,
                    correction_rules,
                    coordinate_settings,
                    text_formatting,
                ),
                update_progress,
                set_definition=export_definition,
                set_resolver=set_resolver,
                cancel_check=self._is_operation_cancelled,
            )

        def on_success(result: ExportResult) -> None:
            logger.info(
                "Excel export result | images=%d | errors=%d | notices=%d",
                result.total_images,
                len(result.errors),
                len(result.notices),
            )
            self.last_export_result = result
            self.last_export_signature = export_signature
            self.last_export_output_path = output_path
            self.last_export_format = "excel"
            self.image_queue.apply_result(result)
            self._render_side_body()
            if result.errors:
                self.status_var.set(
                    f"Excelへ出力しました（エラー {len(result.errors)} 件）: {output_path}"
                )
                self.dialogs.showwarning(
                    "完了（エラーあり）",
                    f"{result.total_images} 画像の処理が完了しました。"
                    f"\nエラー {len(result.errors)} 件は Errors シートに出力しました。"
                    + (
                        f"\n除外通知 {len(result.notices)} 件は Notices シートに出力しました。"
                        if result.notices
                        else ""
                    )
                    + f"\n{output_path}",
                )
            else:
                notice_suffix = (
                    f"（除外通知 {len(result.notices)} 件）" if result.notices else ""
                )
                self.status_var.set(f"Excelへ出力しました{notice_suffix}: {output_path}")
                self.dialogs.showinfo(
                    "完了",
                    f"{result.total_images} 画像をExcelへ出力しました。"
                    + (
                        f"\n除外通知 {len(result.notices)} 件は Notices シートに出力しました。"
                        if result.notices
                        else ""
                    )
                    + f"\n{output_path}",
                )

        def on_error(error: Exception) -> None:
            self.image_queue.prepare(image_files)
            self._render_side_body()
            self.status_var.set("Excel出力に失敗しました。")
            self.dialogs.showerror(
                "出力エラー", f"Excelへ出力できませんでした。\n{error}"
            )

        def on_finally() -> None:
            self.progress_var.set("")

        def on_cancel() -> None:
            self.image_queue.prepare(image_files)
            self._render_side_body()
            self.status_var.set("Excel出力をキャンセルしました。ファイルは変更されていません。")

        self._run_background(
            "Excelへ出力しています",
            work,
            on_success,
            on_error,
            on_finally,
            on_cancel,
            loading_note=f"0 / {total} 画像\n出力の準備をしています。",
            loading_progress=0.0,
        )

    def retry_failed_images(self) -> None:
        if self.last_export_result is None or self.last_export_output_path is None:
            self.dialogs.showinfo(
                "再実行対象なし",
                "先にExcelまたはCSV出力を実行してください。",
            )
            return
        retry_files = self.image_queue.failed_files()
        if not retry_files:
            self.dialogs.showinfo("再実行対象なし", "再実行が必要な画像はありません。")
            return
        fields = self._enabled_fields()
        set_error = self._validate_set_export(fields)
        if set_error:
            self.dialogs.showerror("セット設定エラー", set_error)
            return
        export_format = getattr(self, "last_export_format", "excel") or "excel"
        if export_format == "csv":
            settings = self._build_csv_export_settings()
        else:
            settings, error = self._build_export_settings()
            if error or settings is None:
                self.dialogs.showerror(
                    "出力設定エラー", error or "Excel出力設定を確認してください。"
                )
                return
        current_signature = self._export_structure_signature(fields, settings)
        if (
            (
                export_format == "excel"
                and self.output_path != self.last_export_output_path
            )
            or current_signature != self.last_export_signature
        ):
            destination_label = (
                "ファイル名列、ヘッダー行、Excelの行単位・出力列"
                if export_format == "csv"
                else "出力先、シート、開始セル、Excelの行単位・出力列"
            )
            self.dialogs.showerror(
                "再実行できません",
                f"{destination_label}のいずれかが"
                "前回の出力から変更されています。\n"
                f"全画像を{export_format.upper() if export_format == 'csv' else 'Excel'}"
                "出力し直してください。",
            )
            return
        if not self._require_ocr_ready(self.retry_failed_images):
            return

        output_path = self.last_export_output_path
        previous_result = self.last_export_result
        export_fields = [self._field_for_ocr(field) for field in fields]
        coordinate_settings = replace(self.coordinate_settings)
        lang = self.lang_var.get()
        backend_config = ""
        backend = self.profile.default_backend
        correction_rules = [replace(rule) for rule in self.correction_rules]
        auto_detection = self.profile.auto_detection
        text_formatting = self.text_formatting
        export_definition = self.set_definition
        set_resolver = None
        if (
            auto_detection is not None
            and settings.output_layout == EXPORT_LAYOUT_SET
            and self.auto_detection_enabled
            and export_definition.preset == self.profile.default_set_preset
        ):
            set_resolver = lambda image, detection_fields: cast(
                SetDetectionResult,
                auto_detection.resolve_sets(
                    image,
                    detection_fields,
                    coordinate_settings,
                ),
            )
        total = len(retry_files)
        self.progress_var.set(f"0 / {total}")
        self.status_var.set(f"失敗した {total} 画像を再実行します。")
        self.image_queue.prepare(retry_files)

        def update_progress(row_index: int, total_count: int, image_path: Path) -> None:
            def apply_update() -> None:
                self.image_queue.mark_processing(image_path)
                self._refresh_queue_item_status(image_path)
                self.progress_var.set(f"{row_index} / {total_count}")
                self.status_var.set(f"再OCR中: {image_path.name}")
                self._update_loading(
                    "失敗した画像を再実行しています",
                    note=f"{row_index} / {total_count} 画像\n{image_path.name}",
                    progress=row_index / max(1, total_count),
                )

            self.root.after(0, apply_update)

        def work():
            exporter = (
                self.csv_exporter
                if export_format == "csv"
                else self.excel_exporter
            )
            return exporter.retry_failed(
                output_path,
                previous_result,
                export_fields,
                settings,
                lambda image, field: self.ocr_engine.recognize_field(
                    image,
                    field,
                    lang,
                    backend_config,
                    backend,
                    correction_rules,
                    coordinate_settings,
                    text_formatting,
                ),
                retry_files=retry_files,
                progress_callback=update_progress,
                set_definition=export_definition,
                set_resolver=set_resolver,
                cancel_check=self._is_operation_cancelled,
            )

        def on_success(result: ExportResult) -> None:
            logger.info(
                "%s retry result | retried=%d | remaining_errors=%d",
                export_format.upper(),
                total,
                len(result.errors),
            )
            self.last_export_result = result
            self.image_queue.apply_result(result)
            self._render_side_body()
            remaining = len(self.image_queue.failed_files())
            if remaining:
                self.status_var.set(
                    f"再実行が完了しました（要再実行 {remaining} 画像）: {output_path}"
                )
                self.dialogs.showwarning(
                    "再実行完了",
                    f"{total} 画像を再実行しました。\n要再実行: {remaining} 画像\n{output_path}",
                )
            else:
                self.status_var.set(f"失敗分の再実行が完了しました: {output_path}")
                self.dialogs.showinfo(
                    "再実行完了", f"{total} 画像の再実行が完了しました。\n{output_path}"
                )

        def on_error(error: Exception) -> None:
            self.image_queue.apply_result(previous_result)
            self._render_side_body()
            self.status_var.set("失敗分の再実行に失敗しました。")
            self.dialogs.showerror(
                "再実行エラー", f"失敗分を再実行できませんでした。\n{error}"
            )

        def on_finally() -> None:
            self.progress_var.set("")

        def on_cancel() -> None:
            self.image_queue.apply_result(previous_result)
            self._render_side_body()
            format_label = "CSV" if export_format == "csv" else "Excel"
            self.status_var.set(
                f"失敗分の再実行をキャンセルしました。"
                f"{format_label}は変更されていません。"
            )

        self._run_background(
            "失敗した画像を再実行しています",
            work,
            on_success,
            on_error,
            on_finally,
            on_cancel,
            loading_note=f"0 / {total} 画像\n再実行の準備をしています。",
            loading_progress=0.0,
        )

    def _validate_set_export(self, fields: list[TemplateField]) -> str | None:
        if self.export_layout_var.get() != EXPORT_LAYOUT_SET:
            return None
        if (
            self.auto_detection_enabled
            and self.set_definition.preset == self.profile.default_set_preset
            and self.profile.auto_detection is not None
            and len(fields) >= 2
        ):
            return None
        return set_validation_error(fields, self.set_definition)

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
        self._ensure_result_buffers()

    def _ensure_result_buffers(self) -> None:
        if len(self.current_results) < len(self.fields):
            self.current_results.extend(
                [""] * (len(self.fields) - len(self.current_results))
            )
        elif len(self.current_results) > len(self.fields):
            self.current_results = self.current_results[: len(self.fields)]
        if len(self.current_raw_results) < len(self.fields):
            self.current_raw_results.extend(
                [""] * (len(self.fields) - len(self.current_raw_results))
            )
        elif len(self.current_raw_results) > len(self.fields):
            self.current_raw_results = self.current_raw_results[: len(self.fields)]

    def _ensure_review_selection(self) -> None:
        if (
            self.selected_index is not None
            and 0 <= self.selected_index < len(self.fields)
            and self.fields[self.selected_index].enabled
        ):
            return
        for idx, field in enumerate(self.fields):
            if field.enabled:
                self.selected_index = idx
                return
        self.selected_index = None

    def _field_order_label(self, idx: int) -> str:
        if (
            self.export_layout_var.get() == EXPORT_LAYOUT_SET
            and 0 <= idx < len(self.fields)
            and self.fields[idx].enabled
        ):
            field = self.fields[idx]
            if (
                field.set_id > 0
                and field.slot_key in self.set_definition.allowed_slot_keys()
            ):
                label = self.set_definition.slot_label(field.slot_key)
                return f"{field.set_id}{label[:1]}"
            return "未割当"
        return f"#{idx + 1}"

    def _field_size_text(self, field: TemplateField) -> str:
        normalized = field.normalized()
        size_text = (
            f"{normalized.x2 - normalized.x1} x {normalized.y2 - normalized.y1}px"
        )
        source_text = (
            f" / 基準 {normalized.source_width}x{normalized.source_height}"
            if normalized.source_width and normalized.source_height
            else ""
        )
        return f"{size_text}{source_text}"

    def _field_compact_size_text(self, field: TemplateField) -> str:
        normalized = field.normalized()
        return f"{normalized.x2 - normalized.x1}×{normalized.y2 - normalized.y1}px"

    def _unique_field_name(self, name: str, skip_index: int | None = None) -> str:
        used = {
            field.name for idx, field in enumerate(self.fields) if idx != skip_index
        }
        if name not in used:
            return name
        base = name
        count = 2
        while f"{base} {count}" in used:
            count += 1
        return f"{base} {count}"

    def _next_field_name(self) -> str:
        index = len(self.fields) + 1
        while True:
            name = f"項目{index}"
            if all(field.name != name for field in self.fields):
                return name
            index += 1

    def _lang_display(self, value: str) -> str:
        labels = {
            "jpn+eng": "日本語 + English",
            "jpn": "日本語のみ",
            "eng": "English のみ",
        }
        return labels.get(value, value or self._lang_display(self.profile.default_lang))

    def _lang_value(self, display: str) -> str:
        values = {
            "日本語 + English": "jpn+eng",
            "日本語のみ": "jpn",
            "English のみ": "eng",
        }
        return values.get(display, display or self.profile.default_lang)


def main(profile: OcrProfile, app_config: ApplicationConfig) -> None:
    configure_logging(app_directory_name=app_config.data_directory_name)
    install_exception_hooks()
    root = ctk.CTk()
    ImageOcrExcelApp(root, profile, app_config)
    try:
        root.mainloop()
    finally:
        logger.info("Application stopped")
        shutdown_logging()


