from __future__ import annotations

from ImageOCR2Excel.models import DEFAULT_SET_DEFINITION
from ImageOCR2Excel.profiles.base import CoordinateSettingsValues, OcrProfile


COORDINATE_DEFAULTS: CoordinateSettingsValues = {
    "coordinate_space": "image",
}

FIELD_OCR_DEFAULTS = {
    "ocr_backend": "default",
    "ocr_preprocess": "default",
    "ocr_scale": "auto",
    "ocr_threshold": 180,
    "ocr_psm": 6,
    "ocr_lang": "",
    "ocr_line_split": "none",
    "ocr_line_padding": 12,
    "ocr_line_detect_threshold": 90,
    "ocr_line_detect_min_ratio": 0.015,
    "ocr_line_detect_gap": 10,
}

PROFILE = OcrProfile(
    profile_id="generic",
    name="Generic fixed-format image OCR",
    default_backend="paddle",
    default_lang="jpn",
    coordinate_defaults=COORDINATE_DEFAULTS,
    default_field_ocr=FIELD_OCR_DEFAULTS,
    text_format_defaults={
        "line_join": "fullwidth_space",
        "fullwidth_ascii": False,
    },
    set_definitions=(DEFAULT_SET_DEFINITION,),
    default_set_preset=DEFAULT_SET_DEFINITION.preset,
)
