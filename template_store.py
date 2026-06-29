from __future__ import annotations

import json
from pathlib import Path

from ocr_models import TEMPLATE_VERSION, TemplateField, field_from_dict


def build_template_data(
    fields: list[TemplateField],
    lang: str,
    tesseract_path: str,
    sample_image: str,
    output_settings: dict,
) -> dict:
    return {
        "version": TEMPLATE_VERSION,
        "lang": lang,
        "tesseract_path": tesseract_path,
        "sample_image": sample_image,
        "output_settings": output_settings,
        "fields": [field.to_dict() for field in fields],
    }


def save_template(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_template(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fields_from_template(data: dict) -> list[TemplateField]:
    return [field_from_dict(item) for item in data.get("fields", [])]
