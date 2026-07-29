from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ImageOCR2Excel.persistence import atomic_write_text
from ImageOCR2Excel.models import (
    DEFAULT_SET_DEFINITION,
    EXPORT_LAYOUT_OPTIONS,
    EXPORT_LAYOUT_SET,
    LINE_JOIN_OPTIONS,
    OCR_LINE_SPLIT_OPTIONS,
    CoordinateSettings,
    TEMPLATE_VERSION,
    CorrectionRule,
    SetDefinition,
    TemplateField,
    TextFormattingSettings,
    coordinate_settings_from_dict,
    correction_rule_from_dict,
    field_from_dict,
    set_definition_from_dict,
    set_validation_error,
    text_formatting_settings_from_dict,
)
from ImageOCR2Excel.set_definitions import example_set_definition


TEMPLATE_FORMAT = "image-ocr-to-excel-template"


class TemplateValidationError(ValueError):
    pass


def _require_mapping(data: dict, key: str) -> dict:
    value = data.get(key)
    if not isinstance(value, dict):
        raise TemplateValidationError(f"'{key}' はオブジェクトで指定してください。")
    return value


def validate_template_data(data: object, expected_profile_id: str | None = None) -> dict:
    if not isinstance(data, dict):
        raise TemplateValidationError("テンプレートのルートはJSONオブジェクトで指定してください。")
    if data.get("format") != TEMPLATE_FORMAT:
        raise TemplateValidationError(f"未対応のテンプレート形式です。format は '{TEMPLATE_FORMAT}' が必要です。")
    version = data.get("version")
    if type(version) is not int or version != TEMPLATE_VERSION:
        raise TemplateValidationError(f"未対応のテンプレートバージョンです。version {TEMPLATE_VERSION} が必要です。")

    profile_id = data.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise TemplateValidationError("profile_id を指定してください。")
    if expected_profile_id and profile_id != expected_profile_id:
        raise TemplateValidationError(
            f"このテンプレートはプロファイル '{profile_id}' 用です。現在のプロファイルは '{expected_profile_id}' です。"
        )
    if not isinstance(data.get("template_name"), str) or not data["template_name"].strip():
        raise TemplateValidationError("template_name を指定してください。")
    if not isinstance(data.get("lang"), str) or not data["lang"].strip():
        raise TemplateValidationError("lang を指定してください。")
    if data.get("ocr_backend") != "paddle":
        raise TemplateValidationError("ocr_backend は 'paddle' を指定してください。")

    output_settings = _require_mapping(data, "output_settings")
    for key in ("sheet_name", "write_mode", "start_cell", "include_filename", "include_header"):
        if key not in output_settings:
            raise TemplateValidationError(f"output_settings.{key} がありません。")
    if output_settings.get("output_layout") not in EXPORT_LAYOUT_OPTIONS:
        raise TemplateValidationError("output_settings.output_layout が不正です。")
    set_definition_data = _require_mapping(data, "set_definition")
    if not str(set_definition_data.get("preset") or "").strip():
        raise TemplateValidationError("set_definition.preset を指定してください。")
    for key in ("name", "order_label"):
        if not str(set_definition_data.get(key) or "").strip():
            raise TemplateValidationError(f"set_definition.{key} を指定してください。")
    columns = set_definition_data.get("columns")
    if not isinstance(columns, list) or not columns:
        raise TemplateValidationError("set_definition.columns に1件以上の列を指定してください。")
    column_keys: list[str] = []
    for index, column in enumerate(columns, start=1):
        if not isinstance(column, dict):
            raise TemplateValidationError(f"set_definition.columns[{index}] はオブジェクトで指定してください。")
        if not str(column.get("key") or "").strip() or not str(column.get("label") or "").strip():
            raise TemplateValidationError(f"set_definition.columns[{index}] に key と label を指定してください。")
        if column.get("ocr_line_split") not in OCR_LINE_SPLIT_OPTIONS:
            raise TemplateValidationError(f"set_definition.columns[{index}].ocr_line_split が不正です。")
        column_keys.append(str(column["key"]).strip())
    if len(column_keys) != len(set(column_keys)):
        raise TemplateValidationError("set_definition.columns の key が重複しています。")
    extra_slots = set_definition_data.get("extra_slots", [])
    if not isinstance(extra_slots, list):
        raise TemplateValidationError(
            "set_definition.extra_slots は配列で指定してください。"
        )
    extra_keys: list[str] = []
    for index, column in enumerate(extra_slots, start=1):
        if not isinstance(column, dict):
            raise TemplateValidationError(
                f"set_definition.extra_slots[{index}] はオブジェクトで指定してください。"
            )
        key = str(column.get("key") or "").strip()
        label = str(column.get("label") or "").strip()
        if not key or not label:
            raise TemplateValidationError(
                f"set_definition.extra_slots[{index}] に key と label を指定してください。"
            )
        if column.get("ocr_line_split") not in OCR_LINE_SPLIT_OPTIONS:
            raise TemplateValidationError(
                f"set_definition.extra_slots[{index}].ocr_line_split が不正です。"
            )
        extra_keys.append(key)
    all_slot_keys = [*column_keys, *extra_keys]
    if len(all_slot_keys) != len(set(all_slot_keys)):
        raise TemplateValidationError(
            "set_definition のスロットkeyが重複しています。"
        )
    additional_rows = set_definition_data.get("additional_rows", [])
    if not isinstance(additional_rows, list):
        raise TemplateValidationError(
            "set_definition.additional_rows は配列で指定してください。"
        )
    for index, row in enumerate(additional_rows, start=1):
        if (
            not isinstance(row, list)
            or len(row) != len(column_keys)
            or any(str(key) not in all_slot_keys for key in row)
        ):
            raise TemplateValidationError(
                f"set_definition.additional_rows[{index}] が不正です。"
            )
    _require_mapping(data, "coordinate_settings")
    text_formatting = data.get("text_formatting")
    if text_formatting is not None:
        if not isinstance(text_formatting, dict):
            raise TemplateValidationError("text_formatting はオブジェクトで指定してください。")
        if text_formatting.get("line_join") not in LINE_JOIN_OPTIONS:
            raise TemplateValidationError("text_formatting.line_join が不正です。")
        if type(text_formatting.get("fullwidth_ascii")) is not bool:
            raise TemplateValidationError("text_formatting.fullwidth_ascii は真偽値で指定してください。")
    profile_options = data.get("profile_options")
    if profile_options is not None:
        if not isinstance(profile_options, dict):
            raise TemplateValidationError("profile_options はオブジェクトで指定してください。")
        if (
            "image_text_correction" in profile_options
            and type(profile_options["image_text_correction"]) is not bool
        ):
            raise TemplateValidationError(
                "profile_options.image_text_correction は真偽値で指定してください。"
            )

    correction_rules = data.get("correction_rules", [])
    if not isinstance(correction_rules, list) or any(not isinstance(item, dict) for item in correction_rules):
        raise TemplateValidationError("correction_rules はオブジェクトの配列で指定してください。")
    fields = data.get("fields")
    if not isinstance(fields, list) or not fields:
        raise TemplateValidationError("fields に1件以上の読み取り項目を指定してください。")
    required_field_keys = {"name", "x1", "y1", "x2", "y2"}
    for index, item in enumerate(fields, start=1):
        if not isinstance(item, dict):
            raise TemplateValidationError(f"fields[{index}] はオブジェクトで指定してください。")
        missing = required_field_keys - item.keys()
        if missing:
            raise TemplateValidationError(f"fields[{index}] に必要な項目がありません: {', '.join(sorted(missing))}")
        if not isinstance(item["name"], str) or not item["name"].strip():
            raise TemplateValidationError(f"fields[{index}].name を指定してください。")
        if any(type(item[key]) is not int for key in ("x1", "y1", "x2", "y2")):
            raise TemplateValidationError(f"fields[{index}] の座標は整数で指定してください。")
        if "set_id" not in item or type(item["set_id"]) is not int or item["set_id"] < 0:
            raise TemplateValidationError(f"fields[{index}].set_id は0以上の整数で指定してください。")
        if "slot_key" not in item or not isinstance(item["slot_key"], str):
            raise TemplateValidationError(f"fields[{index}].slot_key を文字列で指定してください。")
    if output_settings.get("output_layout") == EXPORT_LAYOUT_SET:
        definition = set_definition_from_dict(
            set_definition_data,
            _template_set_fallback(str(set_definition_data["preset"])),
        )
        error = set_validation_error([field_from_dict(item) for item in fields], definition)
        if error:
            raise TemplateValidationError(error)
    return data


def build_template_data(
    fields: list[TemplateField],
    lang: str,
    ocr_backend: str,
    output_settings: dict,
    correction_rules: list[CorrectionRule] | None = None,
    coordinate_settings: CoordinateSettings | None = None,
    text_formatting: TextFormattingSettings | None = None,
    profile_options: dict | None = None,
    set_definition: SetDefinition | None = None,
    template_name: str = "ocr-template",
    profile_id: str = "",
) -> dict:
    return {
        "format": TEMPLATE_FORMAT,
        "version": TEMPLATE_VERSION,
        "template_name": template_name,
        "profile_id": profile_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "lang": lang,
        "ocr_backend": ocr_backend,
        "output_settings": output_settings,
        "coordinate_settings": (coordinate_settings or CoordinateSettings()).to_dict(),
        "text_formatting": (text_formatting or TextFormattingSettings()).to_dict(),
        "profile_options": dict(profile_options or {}),
        "set_definition": (
            set_definition or DEFAULT_SET_DEFINITION
        ).to_dict(),
        "correction_rules": [rule.to_dict() for rule in correction_rules or [] if rule.pattern.strip()],
        "fields": [field.to_dict() for field in fields],
    }


def save_template(path: Path, data: dict) -> None:
    validate_template_data(data)
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_template(path: Path, expected_profile_id: str | None = None) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateValidationError(f"JSONとして読み込めません: {exc.msg}（{exc.lineno}行目）") from exc
    return validate_template_data(data, expected_profile_id)


def fields_from_template(data: dict) -> list[TemplateField]:
    return [field_from_dict(item) for item in data.get("fields", [])]


def correction_rules_from_template(data: dict) -> list[CorrectionRule]:
    return [correction_rule_from_dict(item) for item in data.get("correction_rules", [])]


def coordinate_settings_from_template(data: dict) -> CoordinateSettings:
    return coordinate_settings_from_dict(data.get("coordinate_settings"))


def text_formatting_from_template(
    data: dict,
    fallback: TextFormattingSettings | None = None,
) -> TextFormattingSettings:
    return text_formatting_settings_from_dict(data.get("text_formatting"), fallback)


def profile_options_from_template(data: dict) -> dict:
    value = data.get("profile_options")
    return dict(value) if isinstance(value, dict) else {}


def _template_set_fallback(
    preset: str,
    definitions: tuple[SetDefinition, ...] = (),
) -> SetDefinition:
    match = next(
        (definition for definition in definitions if definition.preset == preset),
        None,
    )
    if match is not None:
        return match
    try:
        return example_set_definition(preset)
    except ValueError:
        return DEFAULT_SET_DEFINITION


def set_definition_from_template(
    data: dict,
    definitions: tuple[SetDefinition, ...] = (),
) -> SetDefinition:
    item = data.get("set_definition")
    preset = str(item.get("preset") or "") if isinstance(item, dict) else ""
    return set_definition_from_dict(
        item,
        _template_set_fallback(preset, definitions),
    )

