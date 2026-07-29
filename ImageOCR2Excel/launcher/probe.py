from __future__ import annotations

import importlib
import importlib.util
import json
import platform
from importlib.metadata import PackageNotFoundError, version


REQUIRED_IMPORTS = (
    "customtkinter",
    "openpyxl",
    "PIL",
    "paddle",
)

REQUIRED_PACKAGES = (
    ("paddleocr", "paddleocr"),
    ("paddle", "paddlepaddle"),
)


def probe() -> dict[str, object]:
    versions: dict[str, str] = {}
    for name in REQUIRED_IMPORTS:
        module = importlib.import_module(name)
        versions[name] = str(getattr(module, "__version__", "available"))
    # Importing paddleocr can initialize its pipeline stack and may touch or
    # acquire model resources. The launcher owns Python packages only, so keep
    # PaddleOCR validation metadata-only and leave model setup to the app.
    for import_name, distribution_name in REQUIRED_PACKAGES:
        if importlib.util.find_spec(import_name) is None:
            raise ModuleNotFoundError(import_name)
        try:
            versions[import_name] = version(distribution_name)
        except PackageNotFoundError as exc:
            raise ModuleNotFoundError(distribution_name) from exc
    return {
        "ok": True,
        "python": platform.python_version(),
        "packages": versions,
    }


def main() -> int:
    print(json.dumps(probe(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

