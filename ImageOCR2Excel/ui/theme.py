from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fonts:
    family: str = "Yu Gothic UI"
    normal: tuple[str, int] = ("Yu Gothic UI", 13)
    small: tuple[str, int] = ("Yu Gothic UI", 12)
    bold: tuple[str, int, str] = ("Yu Gothic UI", 14, "bold")
    title: tuple[str, int, str] = ("Yu Gothic UI", 16, "bold")
    canvas: tuple[str, int, str] = ("Yu Gothic UI", 11, "bold")


@dataclass(frozen=True)
class Palette:
    # Light operational surfaces distinguish ImageOCR2Excel from Megido's dark UI.
    app_bg: str = "#F2F6F6"
    surface: str = "#FFFFFF"
    surface_alt: str = "#E7F0EF"
    input_bg: str = "#FFFFFF"
    border: str = "#C4D5D5"
    border_subtle: str = "#D9E6E5"
    text: str = "#193033"
    muted: str = "#52666A"
    primary: str = "#0F766E"
    primary_hover: str = "#0B625C"
    primary_pressed: str = "#084C47"
    secondary: str = "#E1EBEA"
    secondary_hover: str = "#D3E3E1"
    danger: str = "#B4233C"
    danger_hover: str = "#951B31"
    success: str = "#13795B"
    info: str = "#0F6B8A"
    warning: str = "#A15C00"
    cta: str = "#C2410C"
    cta_hover: str = "#9A3412"
    canvas_bg: str = "#E8F0EF"
    canvas_panel: str = "#F2F6F6"
    canvas_toolbar: str = "#FFFFFF"
    canvas_border: str = "#C4D5D5"
    toolbar_divider: str = "#D5E2E1"
    toolbar_text: str = "#193033"
    toolbar_muted: str = "#5A7073"
    utility_hover: str = "#E1EEEC"
    segmented_bg: str = "#DCE9E7"
    segmented_hover: str = "#CFE0DE"
    selected_row: str = "#DDF1EF"
    dragging_row: str = "#C5E6E2"
    dragging_border: str = "#0E7069"
    disabled_fill: str = "#5A7073"
    enabled_region: str = "#159B89"
    inactive_region: str = "#789294"
    line_region: str = "#087D87"
    line_region_selected: str = "#005B65"
    inactive_line_region: str = "#78A2A3"
    line_guide: str = "#1D6970"
    line_badge: str = "#2F7077"
    set_outline: str = "#668487"
    set_outline_selected: str = "#C2410C"
    set_handle: str = "#0F766E"
    set_handle_hover: str = "#0B625C"
    empty_title: str = "#193033"
    empty_note: str = "#52666A"
    modal_backdrop: str = "#233F42"
    modal_button_hover: str = "#D8E6E5"
    on_color: str = "#ffffff"


@dataclass(frozen=True)
class Layout:
    window_size: str = "1220x800"
    min_window_size: tuple[int, int] = (1040, 680)
    side_panel_width: int = 390
    panel_radius: int = 8
    control_radius: int = 6
    button_height: int = 32
    row_button_height: int = 34
    entry_height: int = 32
    primary_button_height: int = 38
    export_button_height: int = 40
    toolbar_height: int = 54
    status_bar_height: int = 34
    settings_size: str = "440x500"
    modal_backdrop_alpha: float = 0.66


@dataclass(frozen=True)
class Copy:
    initial_status: str = "開始方法を選び、画像フォルダを選択してください。"
    set_layout_note: str = (
        "関連する項目を「セット」にまとめ、セットごとにExcelの1行として出力します。"
        "1画像に複数のセットを設定できます。"
    )
    image_layout_note: str = (
        "1枚の画像から読み取った項目を、Excelの1行にまとめます。"
    )
    recognition_test_note: str = (
        "現在の画像でOCR結果を確認します。Excel出力時は全画像を改めて認識します。"
    )
    saved_template_hint: str = (
        "保存済みテンプレートは右上のメニューから読み込めます。"
    )
    empty_canvas_title: str = "画像フォルダが選択されていません"
    empty_canvas_note: str = "上部のボタンから、スクリーンショットのフォルダを選択します。"
    new_template_label: str = "新規テンプレート（未保存）"


@dataclass(frozen=True)
class UiTheme:
    fonts: Fonts = Fonts()
    palette: Palette = Palette()
    layout: Layout = Layout()
    copy: Copy = Copy()


THEME = UiTheme()

