from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from tkinter import BOTH, Event, Misc, TclError, Toplevel
from typing import Any, cast

import customtkinter as ctk


class ModalOverlay:
    """Reusable modal layer with a translucent backdrop and opaque content."""

    _TRANSPARENT_KEY = "#010203"

    def __init__(
        self,
        root: ctk.CTk,
        *,
        width: int,
        backdrop_color: str,
        backdrop_alpha: float,
        surface_color: str,
        border_color: str,
        corner_radius: int,
    ) -> None:
        self.root = root
        self._width = width
        self._backdrop_alpha = max(0.0, min(1.0, backdrop_alpha))
        self._visible = False
        self._destroyed = False
        self._default_focus: Misc | None = None
        self._default_action: Any | None = None
        self._previous_focus: Misc | None = None
        self._escape_handler: Callable[[], object] | None = None
        self._showing = False
        self._show_job: str | None = None
        self._sync_job: str | None = None
        self._raise_job: str | None = None
        self._topmost_job: str | None = None
        self._focus_job: str | None = None
        self._focus_order: tuple[Misc, ...] = ()
        self._root_bindings: list[tuple[str, str]] = []

        self.backdrop = Toplevel(root)
        self.backdrop.withdraw()
        self.backdrop.overrideredirect(True)
        self.backdrop.transient(root)
        self.backdrop.configure(bg=backdrop_color, cursor="arrow")
        self.backdrop.attributes("-alpha", self._backdrop_alpha)

        self.window = Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.transient(root)

        panel_background = backdrop_color
        if sys.platform.startswith("win"):
            try:
                self.window.configure(bg=self._TRANSPARENT_KEY)
                self.window.attributes("-transparentcolor", self._TRANSPARENT_KEY)
                panel_background = self._TRANSPARENT_KEY
            except TclError:
                self.window.configure(bg=backdrop_color)
        else:
            self.window.configure(bg=backdrop_color)

        self.panel = ctk.CTkFrame(
            self.window,
            width=width,
            corner_radius=corner_radius,
            bg_color=panel_background,
            fg_color=surface_color,
            border_width=1,
            border_color=border_color,
        )
        self.panel.pack(fill=BOTH, expand=True)

        self.window.bind("<Escape>", self._on_escape)
        self.window.bind("<Return>", self._on_activate)
        self.window.bind("<KP_Enter>", self._on_activate)
        self.window.bind("<FocusIn>", self._on_modal_focus_in, add="+")
        self.window.bind("<FocusOut>", self._on_modal_focus_out, add="+")
        self.window.bind("<Tab>", lambda event: self._on_tab(event, reverse=False))
        self.window.bind(
            "<Shift-Tab>", lambda event: self._on_tab(event, reverse=True)
        )
        if not sys.platform.startswith("win"):
            try:
                self.window.bind(
                    "<ISO_Left_Tab>",
                    lambda event: self._on_tab(event, reverse=True),
                )
            except TclError:
                pass
        self.backdrop.bind("<ButtonPress>", lambda _event: "break")
        self._bind_root("<Configure>", self._on_root_configure)
        self._bind_root("<Map>", self._on_root_map)
        self._bind_root("<Unmap>", self._on_root_unmap)
        self._bind_root("<FocusIn>", self._on_root_focus_in)
        self._bind_root("<FocusOut>", self._on_root_focus_out)
        self._bind_root("<Destroy>", self._on_root_destroy)

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def backdrop_alpha(self) -> float:
        return self._backdrop_alpha

    def set_escape_handler(self, handler: Callable[[], object] | None) -> None:
        self._escape_handler = handler

    def set_default_action(self, widget: Misc | None) -> None:
        """Set the action used by Enter when no action button owns focus."""
        self._default_action = widget

    def refresh_geometry(self) -> None:
        """Recenter the modal after its content size changes."""
        self._schedule_sync()

    def set_focus_order(
        self, widgets: Sequence[Misc], *, default: Misc | None = None
    ) -> None:
        self._focus_order = tuple(widgets)
        self._default_focus = default or (
            self._focus_order[0] if self._focus_order else None
        )

    def show(self, *, focus: Misc | None = None) -> None:
        if self._destroyed:
            return
        if not self._visible:
            current_focus = self.root.focus_get()
            self._previous_focus = current_focus if current_focus is not None else None
        self._visible = True
        if focus is not None:
            self._default_focus = focus
        self._show_windows()

    def hide(self) -> None:
        if self._destroyed:
            return
        self._visible = False
        self._cancel_jobs()
        self._release_grab()
        self._set_topmost(False)
        self.window.withdraw()
        self.backdrop.withdraw()
        self._restore_focus()

    def destroy(self) -> None:
        if self._destroyed:
            return
        self.hide()
        for sequence, binding_id in self._root_bindings:
            self.root.unbind(sequence, binding_id)
        self._root_bindings.clear()
        self.window.destroy()
        self.backdrop.destroy()
        self._destroyed = True

    def _bind_root(self, sequence: str, handler: Callable[[Event], object]) -> None:
        binding_id = self.root.bind(sequence, handler, add="+")
        if binding_id:
            self._root_bindings.append((sequence, binding_id))

    def _move_focus(self, target: Misc) -> str:
        if self._visible and target.winfo_exists():
            try:
                target.focus_set()
            except (AttributeError, TclError):
                pass
        return "break"

    def _on_tab(self, _event: Event, *, reverse: bool) -> str:
        if not self._visible or not self._focus_order:
            return "break"
        current = self.root.focus_get()
        index = next(
            (
                position
                for position, widget in enumerate(self._focus_order)
                if self._owns_focus(widget, current)
            ),
            None,
        )
        if index is None:
            target = self._default_focus or self._focus_order[0]
        else:
            offset = -1 if reverse else 1
            target = self._focus_order[(index + offset) % len(self._focus_order)]
        return self._move_focus(target)

    @staticmethod
    def _owns_focus(widget: Misc, focused: Misc | None) -> bool:
        current = focused
        while current is not None:
            if current is widget:
                return True
            current = getattr(current, "master", None)
        return False

    def _show_windows(self) -> None:
        self._show_job = None
        if not self._visible or self._destroyed or self._showing:
            return
        self._showing = True
        try:
            try:
                if self.root.state() in {"withdrawn", "iconic"}:
                    return
            except TclError:
                return

            self._sync_geometry()
            self.backdrop.deiconify()
            self.window.deiconify()
            self._raise_windows()
            self._schedule_raise()
            # On Windows, a Tk input grab also blocks the owner's native title-bar
            # controls.  The backdrop already covers the root's client area, so
            # keeping the grab there only prevents users from closing the app via
            # its own close button while a modal is visible.
            if not sys.platform.startswith("win"):
                try:
                    self.window.grab_set()
                except TclError:
                    return
            self._schedule_focus()
        finally:
            self._showing = False

    def _schedule_show(self) -> None:
        if (
            not self._visible
            or self._destroyed
            or self._show_job is not None
        ):
            return
        self._show_job = self.root.after_idle(self._show_windows)

    def _schedule_sync(self) -> None:
        if not self._visible or self._destroyed or self._sync_job is not None:
            return
        self._sync_job = self.root.after_idle(self._sync_geometry)

    def _schedule_raise(self) -> None:
        if (
            not self._visible
            or self._destroyed
            or self._raise_job is not None
        ):
            return
        self._raise_job = self.root.after_idle(self._raise_windows)

    def _raise_windows(self) -> None:
        self._raise_job = None
        if not self._visible or self._destroyed:
            return
        try:
            self._set_topmost(True)
            self.backdrop.lift(self.root)
            self.window.lift(self.backdrop)
        except TclError:
            return

    def _set_topmost(self, enabled: bool) -> None:
        if not sys.platform.startswith("win"):
            return
        try:
            self.backdrop.attributes("-topmost", enabled)
            self.window.attributes("-topmost", enabled)
        except TclError:
            return

    def _schedule_topmost_check(self) -> None:
        if not sys.platform.startswith("win") or self._topmost_job is not None:
            return
        self._topmost_job = self.root.after(100, self._sync_topmost_to_focus)

    def _sync_topmost_to_focus(self) -> None:
        self._topmost_job = None
        if not self._visible or self._destroyed:
            return
        try:
            focused = self.root.focus_get()
        except TclError:
            focused = None
        self._set_topmost(focused is not None)
        if focused is not None:
            self._raise_windows()

    def _sync_geometry(self) -> None:
        self._sync_job = None
        if not self._visible or self._destroyed:
            return
        try:
            self.root.update_idletasks()
            root_width = max(1, self.root.winfo_width())
            root_height = max(1, self.root.winfo_height())
            root_x = self.root.winfo_rootx()
            root_y = self.root.winfo_rooty()
            self.backdrop.geometry(
                self._geometry(root_width, root_height, root_x, root_y)
            )

            self.window.update_idletasks()
            dialog_width = max(self._width, self.panel.winfo_reqwidth())
            dialog_height = max(1, self.panel.winfo_reqheight())
            dialog_x = root_x + (root_width - dialog_width) // 2
            dialog_y = root_y + (root_height - dialog_height) // 2
            self.window.geometry(
                self._geometry(
                    dialog_width, dialog_height, dialog_x, dialog_y
                )
            )
        except TclError:
            return

    @staticmethod
    def _geometry(width: int, height: int, x: int, y: int) -> str:
        x_text = f"+{x}" if x >= 0 else str(x)
        y_text = f"+{y}" if y >= 0 else str(y)
        return f"{width}x{height}{x_text}{y_text}"

    def _schedule_focus(self) -> None:
        if self._focus_job is not None:
            self.root.after_cancel(self._focus_job)
        self._focus_job = self.root.after_idle(self._focus_default)

    def _focus_default(self) -> None:
        self._focus_job = None
        target = self._default_focus
        if (
            self._visible
            and not self._destroyed
            and target is not None
            and target.winfo_exists()
        ):
            try:
                self._raise_windows()
                self.window.focus_set()
                target.focus_set()
            except (AttributeError, TclError):
                pass

    def _restore_focus(self) -> None:
        target = self._previous_focus
        self._previous_focus = None
        try:
            if target is not None and target.winfo_exists():
                target.focus_set()
            else:
                self.root.focus_set()
        except TclError:
            pass

    def _release_grab(self) -> None:
        try:
            self.window.grab_release()
        except TclError:
            pass

    def _cancel_jobs(self) -> None:
        for attribute in (
            "_show_job",
            "_sync_job",
            "_raise_job",
            "_topmost_job",
            "_focus_job",
        ):
            job = getattr(self, attribute)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except TclError:
                    pass
                setattr(self, attribute, None)

    def _on_escape(self, _event: Event) -> str:
        if self._escape_handler is not None:
            self._escape_handler()
        return "break"

    def _on_activate(self, _event: Event) -> str | None:
        if self._default_action is None:
            return None
        focused = self.root.focus_get()
        target = cast(
            Any,
            next(
                (
                    widget
                    for widget in self._focus_order
                    if self._owns_focus(widget, focused)
                    and callable(getattr(widget, "invoke", None))
                ),
                self._default_action,
            ),
        )
        try:
            if target.cget("state") == "disabled":
                target = self._default_action
            if target.cget("state") != "disabled":
                target.invoke()
        except (AttributeError, TclError):
            return "break"
        return "break"

    def _on_root_configure(self, event: Event) -> None:
        if event.widget is self.root:
            self._schedule_sync()

    def _on_root_map(self, event: Event) -> None:
        if event.widget is self.root and self._visible:
            self._schedule_show()

    def _on_root_focus_in(self, event: Event) -> None:
        if event.widget is self.root and self._visible:
            self._set_topmost(True)
            self._schedule_raise()

    def _on_root_focus_out(self, event: Event) -> None:
        if event.widget is self.root and self._visible:
            self._schedule_topmost_check()

    def _on_modal_focus_in(self, _event: Event) -> None:
        if self._visible:
            self._set_topmost(True)
            self._schedule_raise()

    def _on_modal_focus_out(self, _event: Event) -> None:
        if self._visible:
            self._schedule_topmost_check()

    def _on_root_unmap(self, event: Event) -> None:
        if event.widget is not self.root or not self._visible:
            return
        self._cancel_jobs()
        self._release_grab()
        self._set_topmost(False)
        self.window.withdraw()
        self.backdrop.withdraw()

    def _on_root_destroy(self, event: Event) -> None:
        if event.widget is self.root:
            self._cancel_jobs()
            self._destroyed = True

