from __future__ import annotations

from dataclasses import dataclass
from tkinter import Misc, TclError
from typing import Literal

import customtkinter as ctk

from ImageOCR2Excel.ui.icons import ICONS
from ImageOCR2Excel.ui.theme import THEME
from ImageOCR2Excel.ui.modal import ModalOverlay
from ImageOCR2Excel.ui.tooltip import add_tooltip


DialogKind = Literal["info", "warning", "error", "question"]


@dataclass(frozen=True)
class DialogAction:
    text: str
    value: object
    emphasis: Literal["primary", "secondary", "danger"] = "secondary"


class AppDialogs:
    """Synchronous, themed replacements for tkinter.messagebox dialogs."""

    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.active_modal: ModalOverlay | None = None

    def showinfo(self, title: str, message: str) -> str:
        self._show(
            "info",
            title,
            message,
            [DialogAction("OK", "ok", "primary")],
            default_value="ok",
            escape_value="ok",
        )
        return "ok"

    def showwarning(self, title: str, message: str) -> str:
        self._show(
            "warning",
            title,
            message,
            [DialogAction("OK", "ok", "primary")],
            default_value="ok",
            escape_value="ok",
        )
        return "ok"

    def showerror(self, title: str, message: str) -> str:
        self._show(
            "error",
            title,
            message,
            [DialogAction("OK", "ok", "primary")],
            default_value="ok",
            escape_value="ok",
        )
        return "ok"

    def askyesno(
        self,
        title: str,
        message: str,
        *,
        yes_text: str = "はい",
        no_text: str = "いいえ",
        destructive: bool = False,
        kind: DialogKind = "question",
    ) -> bool:
        return bool(
            self._show(
                kind,
                title,
                message,
                [
                    DialogAction(no_text, False),
                    DialogAction(
                        yes_text,
                        True,
                        "danger" if destructive else "primary",
                    ),
                ],
                default_value=False if destructive else True,
                escape_value=False,
            )
        )

    def askyesnocancel(
        self,
        title: str,
        message: str,
        *,
        yes_text: str = "はい",
        no_text: str = "いいえ",
        cancel_text: str = "キャンセル",
        no_destructive: bool = False,
    ) -> bool | None:
        result = self._show(
            "question",
            title,
            message,
            [
                DialogAction(cancel_text, None),
                DialogAction(
                    no_text,
                    False,
                    "danger" if no_destructive else "secondary",
                ),
                DialogAction(yes_text, True, "primary"),
            ],
            default_value=True,
            escape_value=None,
        )
        return result if result is None else bool(result)

    def _show(
        self,
        kind: DialogKind,
        title: str,
        message: str,
        actions: list[DialogAction],
        *,
        default_value: object,
        escape_value: object,
    ) -> object:
        result = escape_value
        previous_grab = self.root.grab_current()
        modal = ModalOverlay(
            self.root,
            width=460,
            backdrop_color=THEME.palette.modal_backdrop,
            backdrop_alpha=THEME.layout.modal_backdrop_alpha,
            surface_color=THEME.palette.surface,
            border_color=THEME.palette.border,
            corner_radius=THEME.layout.panel_radius,
        )
        self.active_modal = modal
        panel = modal.panel
        panel.grid_columnconfigure(1, weight=1)

        icon_name, accent = self._appearance(kind)
        ctk.CTkLabel(
            panel,
            text="",
            image=ICONS.get(icon_name, 24, accent),
            width=28,
            height=28,
        ).grid(row=0, column=0, sticky="nw", padx=(26, 10), pady=(25, 0))
        ctk.CTkLabel(
            panel,
            text=title,
            anchor="w",
            justify="left",
            font=THEME.fonts.title,
            text_color=THEME.palette.text,
        ).grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(24, 0))

        close_button = ctk.CTkButton(
            panel,
            text="",
            image=ICONS.get("x", 16, THEME.palette.muted),
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
        ctk.CTkLabel(
            panel,
            text=message,
            anchor="w",
            justify="left",
            font=THEME.fonts.normal,
            text_color=THEME.palette.muted,
            wraplength=382,
        ).grid(
            row=1,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(0, 26),
            pady=(16, 24),
        )

        action_row = ctk.CTkFrame(panel, fg_color="transparent")
        action_row.grid(
            row=2,
            column=0,
            columnspan=3,
            sticky="e",
            padx=26,
            pady=(0, 24),
        )

        buttons: list[ctk.CTkButton] = []
        default_button: ctk.CTkButton | None = None

        def finish(value: object) -> None:
            nonlocal result
            result = value
            if modal.window.winfo_exists():
                modal.destroy()

        close_button.configure(command=lambda: finish(escape_value))
        modal.set_escape_handler(lambda: finish(escape_value))

        for action in actions:
            fg_color, hover_color, border_width, border_color = self._button_style(
                action.emphasis
            )
            button = ctk.CTkButton(
                action_row,
                text=action.text,
                command=lambda value=action.value: finish(value),
                width=max(112, len(action.text) * 12 + 28),
                height=THEME.layout.primary_button_height,
                corner_radius=THEME.layout.control_radius,
                font=THEME.fonts.small,
                fg_color=fg_color,
                hover_color=hover_color,
                border_width=border_width,
                border_color=border_color,
                text_color=THEME.palette.text,
            )
            button.pack(side="left", padx=(8 if buttons else 0, 0))
            buttons.append(button)
            if action.value == default_value:
                default_button = button

        if default_button is None:
            default_button = buttons[-1]
        def invoke_focused_or_default(_event=None) -> str:
            focused = self.root.focus_get()
            target = next(
                (
                    button
                    for button in [*buttons, close_button]
                    if self._owns_focus(button, focused)
                ),
                default_button,
            )
            target.invoke()
            return "break"

        modal.window.bind("<Return>", invoke_focused_or_default)
        modal.window.bind("<KP_Enter>", invoke_focused_or_default)
        modal.window.bind("<space>", invoke_focused_or_default)
        modal.set_focus_order(buttons + [close_button], default=default_button)
        modal.show(focus=default_button)
        try:
            self.root.wait_window(modal.window)
        except TclError:
            pass
        finally:
            self.active_modal = None
            self._restore_previous_grab(previous_grab)
        return result

    @staticmethod
    def _owns_focus(widget: Misc, focused: Misc | None) -> bool:
        current = focused
        while current is not None:
            if current is widget:
                return True
            current = getattr(current, "master", None)
        return False

    @staticmethod
    def _appearance(kind: DialogKind) -> tuple[str, str]:
        if kind == "error":
            return "alert_circle", THEME.palette.danger
        if kind == "warning":
            return "alert_circle", THEME.palette.warning
        if kind == "question":
            return "circle", THEME.palette.primary
        return "check_circle", THEME.palette.info

    @staticmethod
    def _button_style(emphasis: str) -> tuple[str, str, int, str]:
        if emphasis == "danger":
            return (
                THEME.palette.danger,
                THEME.palette.danger_hover,
                0,
                THEME.palette.danger,
            )
        if emphasis == "primary":
            return (
                THEME.palette.primary,
                THEME.palette.primary_hover,
                0,
                THEME.palette.primary,
            )
        return (
            THEME.palette.secondary,
            THEME.palette.secondary_hover,
            1,
            THEME.palette.border,
        )

    def _restore_previous_grab(self, widget: Misc | None) -> None:
        if widget is None:
            return
        try:
            if widget.winfo_exists() and widget.winfo_viewable():
                widget.grab_set()
        except TclError:
            pass

