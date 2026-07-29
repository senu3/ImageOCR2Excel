from __future__ import annotations

from pathlib import Path

from ImageOCR2Excel.apps.image_ocr import launcher_main


if __name__ == "__main__":
    raise SystemExit(
        launcher_main(project_root=Path(__file__).resolve().parent)
    )
