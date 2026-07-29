from __future__ import annotations

from tkinter import Misc, TclError, Toplevel

import customtkinter as ctk

from ImageOCR2Excel.ui.theme import THEME


class UiTooltip:
    """Delayed, keyboard-aware tooltip for compact desktop controls."""

    def __init__(self, widget: Misc, text: str, *, delay_ms: int = 500) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._show_job: str | None = None
        self._window: Toplevel | None = None

        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<FocusIn>", self._show_from_focus, add="+")
        widget.bind("<FocusOut>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def _schedule(self, _event=None, *, delay_ms: int | None = None) -> None:
        self.hide()
        delay = self.delay_ms if delay_ms is None else delay_ms
        try:
            self._show_job = self.widget.after(delay, self.show)
        except TclError:
            self._show_job = None

    def _show_from_focus(self, _event=None) -> None:
        self._schedule(delay_ms=0)

    def show(self) -> None:
        self._show_job = None
        try:
            if not self.widget.winfo_exists() or not self.widget.winfo_viewable():
                return
            window = Toplevel(self.widget)
            window.withdraw()
            window.overrideredirect(True)
            window.transient(self.widget.winfo_toplevel())
            window.configure(bg=THEME.palette.border)
            try:
                window.attributes("-topmost", True)
            except TclError:
                pass
            label = ctk.CTkLabel(
                window,
                text=self.text,
                height=26,
                corner_radius=4,
                fg_color=THEME.palette.surface_alt,
                text_color=THEME.palette.text,
                font=THEME.fonts.small,
            )
            label.pack(padx=1, pady=1)
            window.update_idletasks()
            x, y = self._position(
                window.winfo_reqwidth(), window.winfo_reqheight()
            )
            x_text = f"+{x}" if x >= 0 else str(x)
            y_text = f"+{y}" if y >= 0 else str(y)
            window.geometry(f"{x_text}{y_text}")
            window.deiconify()
            window.lift()
            self._window = window
        except TclError:
            self._window = None

    def _position(self, width: int, height: int) -> tuple[int, int]:
        x = self.widget.winfo_rootx() + (self.widget.winfo_width() - width) // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        left = self.widget.winfo_vrootx()
        top = self.widget.winfo_vrooty()
        right = left + self.widget.winfo_vrootwidth()
        bottom = top + self.widget.winfo_vrootheight()
        x = min(max(left + 4, x), max(left + 4, right - width - 4))
        if y + height > bottom - 4:
            y = self.widget.winfo_rooty() - height - 6
        y = max(top + 4, y)
        return x, y

    def hide(self, _event=None) -> None:
        if self._show_job is not None:
            try:
                self.widget.after_cancel(self._show_job)
            except TclError:
                pass
            self._show_job = None
        window = self._window
        self._window = None
        if window is not None:
            try:
                window.destroy()
            except TclError:
                pass


def add_tooltip(widget: Misc, text: str) -> UiTooltip:
    tooltip = UiTooltip(widget, text)
    setattr(widget, "_ui_tooltip", tooltip)
    return tooltip

