from __future__ import annotations

import re
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


def natural_image_sort_key(path: Path) -> tuple:
    """Sort numbered screenshot names in the order users expect."""
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", path.name.casefold()))

