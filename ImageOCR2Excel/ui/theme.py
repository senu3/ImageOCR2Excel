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
    app_bg: str = "#071012"
    surface: str = "#10191c"
    surface_alt: str = "#162226"
    input_bg: str = "#0b1417"
    border: str = "#284047"
    border_subtle: str = "#1c3035"
    text: str = "#f3eff8"
    muted: str = "#b5c7ca"
    primary: str = "#0d9488"
    primary_hover: str = "#14a69a"
    primary_pressed: str = "#0f766e"
    secondary: str = "#1a2a2f"
    secondary_hover: str = "#233940"
    danger: str = "#df6176"
    danger_hover: str = "#c94e64"
    success: str = "#59c98c"
    info: str = "#62c6e8"
    warning: str = "#e5b85c"
    cta: str = "#f97316"
    cta_hover: str = "#fb923c"
    canvas_bg: str = "#030304"
    canvas_panel: str = "#071012"
    canvas_toolbar: str = "#0b1417"
    canvas_border: str = "#1c3035"
    toolbar_divider: str = "#243b42"
    toolbar_text: str = "#f3eff8"
    toolbar_muted: str = "#bfb4cb"
    utility_hover: str = "#20343a"
    segmented_bg: str = "#132025"
    segmented_hover: str = "#20343a"
    selected_row: str = "#172d31"
    dragging_row: str = "#203b42"
    dragging_border: str = "#22c7ba"
    disabled_fill: str = "#5f566a"
    enabled_region: str = "#2dd4bf"
    inactive_region: str = "#52787d"
    line_region: str = "#3fd7e8"
    line_region_selected: str = "#6af5ff"
    inactive_line_region: str = "#2e7f8c"
    line_guide: str = "#8ff8ff"
    line_badge: str = "#145467"
    set_outline: str = "#789196"
    set_outline_selected: str = "#f97316"
    set_handle: str = "#0f766e"
    set_handle_hover: str = "#0d9488"
    empty_title: str = "#f3eff8"
    empty_note: str = "#bfb4cb"
    modal_backdrop: str = "#050407"
    modal_button_hover: str = "#282132"
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

