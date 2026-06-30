from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ocr_models import TEMPLATE_VERSION, TemplateField, field_from_dict


def build_template_data(
    fields: list[TemplateField],
    lang: str,
    tesseract_path: str,
    sample_image: str,
    output_settings: dict,
    template_name: str = "ocr-template",
    sample_image_size: dict | None = None,
) -> dict:
    return {
        "format": "image-ocr-to-excel-template",
        "version": TEMPLATE_VERSION,
        "template_name": template_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "lang": lang,
        "tesseract_path": tesseract_path,
        "sample_image": sample_image,
        "sample_image_size": sample_image_size,
        "output_settings": output_settings,
        "fields": [field.to_dict() for field in fields],
    }


def save_template(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_template(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fields_from_template(data: dict) -> list[TemplateField]:
    return [field_from_dict(item) for item in data.get("fields", [])]
