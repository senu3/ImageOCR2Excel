from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk
from PIL import Image, ImageDraw


IconDrawer = Callable[[ImageDraw.ImageDraw, int, int], None]


class IconSet:
    """Small monochrome outline icon set rendered at high resolution for Tk."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, int, str], Image.Image] = {}

    def get(self, name: str, size: int = 18, color: str = "#F3EFF8") -> ctk.CTkImage:
        key = (name, size, color)
        if key not in self._cache:
            scale = 4
            image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            drawer = _DRAWERS.get(name, _draw_circle)
            drawer(draw, size * scale, scale)
            alpha = image.getchannel("A")
            solid = Image.new("RGBA", image.size, color)
            solid.putalpha(alpha)
            self._cache[key] = solid
        image = self._cache[key]
        # CTkImage internally owns Tk PhotoImages, which cannot cross destroyed Tk roots.
        # Return a lightweight wrapper per widget while reusing the rendered PIL bitmap.
        return ctk.CTkImage(light_image=image, dark_image=image, size=(size, size))


def _line(draw: ImageDraw.ImageDraw, points, scale: int, *, width: float = 1.75) -> None:
    draw.line(points, fill="white", width=max(1, round(width * scale)), joint="curve")


def _box(draw: ImageDraw.ImageDraw, xy, scale: int, radius: float = 2.0) -> None:
    draw.rounded_rectangle(xy, radius=radius * scale, outline="white", width=max(1, round(1.75 * scale)))


def _draw_folder(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _line(draw, [(3*scale, 7*scale), (3*scale, 5*scale), (8*scale, 5*scale), (10*scale, 7*scale), (15*scale, 7*scale)], scale)
    _line(draw, [(3*scale, 7*scale), (3*scale, 14*scale), (14*scale, 14*scale), (16*scale, 8*scale), (15*scale, 7*scale)], scale)


def _draw_settings(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    for y, knob in ((5, 7), (9, 12), (13, 9)):
        _line(draw, [(3*scale, y*scale), (15*scale, y*scale)], scale)
        draw.ellipse(((knob-1.2)*scale, (y-1.2)*scale, (knob+1.2)*scale, (y+1.2)*scale), fill="#000000", outline="white", width=max(1, round(1.5*scale)))


def _draw_chevron_left(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _line(draw, [(11*scale, 4*scale), (6*scale, 9*scale), (11*scale, 14*scale)], scale, width=2)


def _draw_chevron_right(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _line(draw, [(7*scale, 4*scale), (12*scale, 9*scale), (7*scale, 14*scale)], scale, width=2)


def _draw_chevron_up(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _line(draw, [(4*scale, 11*scale), (9*scale, 6*scale), (14*scale, 11*scale)], scale, width=2)


def _draw_chevron_down(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _line(draw, [(4*scale, 7*scale), (9*scale, 12*scale), (14*scale, 7*scale)], scale, width=2)


def _draw_save(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _box(draw, (3*scale, 3*scale, 15*scale, 15*scale), scale)
    _box(draw, (6*scale, 3*scale, 12*scale, 8*scale), scale, radius=1)
    _line(draw, [(6*scale, 15*scale), (6*scale, 11*scale), (12*scale, 11*scale), (12*scale, 15*scale)], scale)


def _draw_file_input(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _line(draw, [(5*scale, 3*scale), (11*scale, 3*scale), (14*scale, 6*scale), (14*scale, 15*scale), (5*scale, 15*scale), (5*scale, 11*scale)], scale)
    _line(draw, [(10*scale, 3*scale), (10*scale, 7*scale), (14*scale, 7*scale)], scale)
    _line(draw, [(2*scale, 9*scale), (9*scale, 9*scale), (7*scale, 7*scale), (9*scale, 9*scale), (7*scale, 11*scale)], scale)


def _draw_scan(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _line(draw, [(3*scale, 7*scale), (3*scale, 3*scale), (7*scale, 3*scale)], scale)
    _line(draw, [(11*scale, 3*scale), (15*scale, 3*scale), (15*scale, 7*scale)], scale)
    _line(draw, [(15*scale, 11*scale), (15*scale, 15*scale), (11*scale, 15*scale)], scale)
    _line(draw, [(7*scale, 15*scale), (3*scale, 15*scale), (3*scale, 11*scale)], scale)
    _line(draw, [(6*scale, 7*scale), (12*scale, 7*scale)], scale)
    _line(draw, [(6*scale, 10*scale), (12*scale, 10*scale)], scale)


def _draw_table(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _box(draw, (2.5*scale, 3*scale, 15.5*scale, 15*scale), scale)
    _line(draw, [(3*scale, 7*scale), (15*scale, 7*scale)], scale)
    _line(draw, [(3*scale, 11*scale), (15*scale, 11*scale)], scale)
    _line(draw, [(7*scale, 3*scale), (7*scale, 15*scale)], scale)


def _draw_refresh(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    width = max(1, round(1.75 * scale))
    bounds = (3 * scale, 3 * scale, 15 * scale, 15 * scale)
    draw.arc(bounds, 185, 355, fill="white", width=width)
    draw.arc(bounds, 5, 175, fill="white", width=width)
    _line(
        draw,
        [(3 * scale, 4 * scale), (3 * scale, 8 * scale), (7 * scale, 8 * scale)],
        scale,
    )
    _line(
        draw,
        [(11 * scale, 10 * scale), (15 * scale, 10 * scale), (15 * scale, 14 * scale)],
        scale,
    )


def _draw_arrow_up(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _line(draw, [(9*scale, 15*scale), (9*scale, 3*scale), (4*scale, 8*scale), (9*scale, 3*scale), (14*scale, 8*scale)], scale)


def _draw_arrow_down(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _line(draw, [(9*scale, 3*scale), (9*scale, 15*scale), (4*scale, 10*scale), (9*scale, 15*scale), (14*scale, 10*scale)], scale)


def _draw_plus(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _line(draw, [(9*scale, 3*scale), (9*scale, 15*scale), (9*scale, 9*scale), (3*scale, 9*scale), (15*scale, 9*scale)], scale, width=2)


def _draw_x(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _line(draw, [(4*scale, 4*scale), (14*scale, 14*scale), (9*scale, 9*scale), (14*scale, 4*scale), (4*scale, 14*scale)], scale)


def _draw_trash(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _line(draw, [(3*scale, 5*scale), (15*scale, 5*scale), (7*scale, 5*scale), (7*scale, 3*scale), (11*scale, 3*scale), (11*scale, 5*scale)], scale)
    _line(draw, [(5*scale, 5*scale), (6*scale, 15*scale), (12*scale, 15*scale), (13*scale, 5*scale)], scale)


def _draw_pencil(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _line(draw, [(4*scale, 14*scale), (5*scale, 10*scale), (12*scale, 3*scale), (15*scale, 6*scale), (8*scale, 13*scale), (4*scale, 14*scale)], scale)
    _line(draw, [(10.5*scale, 4.5*scale), (13.5*scale, 7.5*scale)], scale)


def _draw_more(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    for y in (4, 9, 14):
        draw.ellipse((8*scale, (y-1)*scale, 10*scale, (y+1)*scale), fill="white")


def _draw_duplicate(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _box(draw, (5*scale, 5*scale, 15*scale, 15*scale), scale)
    _line(draw, [(3*scale, 12*scale), (3*scale, 3*scale), (12*scale, 3*scale)], scale)


def _draw_grip(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    for x in (6, 12):
        for y in (4, 9, 14):
            draw.ellipse(((x-1)*scale, (y-1)*scale, (x+1)*scale, (y+1)*scale), fill="white")


def _draw_circle(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    draw.ellipse((3*scale, 3*scale, 15*scale, 15*scale), outline="white", width=max(1, round(1.75*scale)))


def _draw_check_circle(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _draw_circle(draw, size, scale)
    _line(draw, [(6*scale, 9*scale), (8*scale, 11*scale), (12.5*scale, 6.5*scale)], scale)


def _draw_alert_circle(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _draw_circle(draw, size, scale)
    _line(draw, [(9*scale, 5.5*scale), (9*scale, 10*scale)], scale, width=2)
    draw.ellipse((8*scale, 12*scale, 10*scale, 14*scale), fill="white")


def _draw_minus_circle(draw: ImageDraw.ImageDraw, size: int, scale: int) -> None:
    _draw_circle(draw, size, scale)
    _line(draw, [(6*scale, 9*scale), (12*scale, 9*scale)], scale, width=2)


_DRAWERS: dict[str, IconDrawer] = {
    "folder": _draw_folder,
    "settings": _draw_settings,
    "chevron_left": _draw_chevron_left,
    "chevron_right": _draw_chevron_right,
    "chevron_up": _draw_chevron_up,
    "chevron_down": _draw_chevron_down,
    "save": _draw_save,
    "file_input": _draw_file_input,
    "scan": _draw_scan,
    "table": _draw_table,
    "refresh": _draw_refresh,
    "arrow_up": _draw_arrow_up,
    "arrow_down": _draw_arrow_down,
    "plus": _draw_plus,
    "x": _draw_x,
    "trash": _draw_trash,
    "pencil": _draw_pencil,
    "more": _draw_more,
    "duplicate": _draw_duplicate,
    "grip": _draw_grip,
    "circle": _draw_circle,
    "check_circle": _draw_check_circle,
    "alert_circle": _draw_alert_circle,
    "minus_circle": _draw_minus_circle,
}


ICONS = IconSet()

