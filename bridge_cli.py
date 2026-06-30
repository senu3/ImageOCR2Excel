from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ocr_models import TemplateField
from template_store import build_template_data, fields_from_template, load_template, save_template


class BridgeError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _safe_file_stem(value: str) -> str:
    sanitized = "".join("-" if character in '\\/:*?"<>|' else "_" if character.isspace() else character for character in value)
    trimmed = sanitized.strip(". _-")
    return trimmed or "ocr-template"


def _as_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _field_from_draft(item: dict[str, Any]) -> TemplateField:
    if {"x1", "y1", "x2", "y2"}.issubset(item):
        source_width = _as_int(item.get("source_width") or item.get("sourceWidth"))
        source_height = _as_int(item.get("source_height") or item.get("sourceHeight"))
        return TemplateField(
            name=str(item.get("name") or "項目"),
            x1=_as_int(item.get("x1")),
            y1=_as_int(item.get("y1")),
            x2=_as_int(item.get("x2")),
            y2=_as_int(item.get("y2")),
            enabled=bool(item.get("enabled", True)),
            source_width=source_width,
            source_height=source_height,
            postprocess=str(item.get("postprocess") or "そのまま"),
            replace_from=str(item.get("replace_from") or ""),
            replace_to=str(item.get("replace_to") or ""),
            remove_text=str(item.get("remove_text") or ""),
        ).normalized()

    region = item.get("region")
    if not isinstance(region, dict):
        raise BridgeError("template.invalid_field", "項目の領域が不正です。", {"field": item.get("id") or item.get("name")})

    source_size = item.get("sourceSize") or item.get("source_size") or {}
    if not isinstance(source_size, dict):
        source_size = {}

    x = _as_int(region.get("x"))
    y = _as_int(region.get("y"))
    width = _as_int(region.get("width"))
    height = _as_int(region.get("height"))

    return TemplateField(
        name=str(item.get("name") or "項目"),
        x1=x,
        y1=y,
        x2=x + width,
        y2=y + height,
        enabled=bool(item.get("enabled", True)),
        source_width=_as_int(source_size.get("width")),
        source_height=_as_int(source_size.get("height")),
        postprocess=str(item.get("postprocess") or "そのまま"),
        replace_from=str(item.get("replace_from") or ""),
        replace_to=str(item.get("replace_to") or ""),
        remove_text=str(item.get("remove_text") or ""),
    ).normalized()


def _draft_field_from_template(field: TemplateField, index: int) -> dict[str, Any]:
    normalized = field.normalized()
    return {
        "id": f"field-{index + 1}",
        "name": normalized.name,
        "enabled": normalized.enabled,
        "order": index + 1,
        "region": {
            "x": normalized.x1,
            "y": normalized.y1,
            "width": normalized.x2 - normalized.x1,
            "height": normalized.y2 - normalized.y1,
        },
        "sourceSize": {
            "width": normalized.source_width,
            "height": normalized.source_height,
        },
        "postprocess": normalized.postprocess,
    }


def _template_to_draft(data: dict[str, Any]) -> dict[str, Any]:
    fields = fields_from_template(data)
    sample_image_size = data.get("sample_image_size")
    if not isinstance(sample_image_size, dict):
        sample_image_size = None

    sample_image = data.get("sample_image") or ""
    sample_path = Path(str(sample_image)) if sample_image else None

    return {
        "template_name": data.get("template_name") or "ocr-template",
        "sample_image": str(sample_image),
        "sample_image_name": sample_path.name if sample_path else "",
        "sample_image_size": sample_image_size,
        "lang": data.get("lang") or "jpn+eng",
        "tesseract_path": data.get("tesseract_path") or "",
        "output_settings": data.get("output_settings") or {},
        "fields": [_draft_field_from_template(field, index) for index, field in enumerate(fields)],
    }


def template_save(payload: dict[str, Any]) -> dict[str, Any]:
    draft = payload.get("draft")
    if not isinstance(draft, dict):
        raise BridgeError("template.invalid_format", "テンプレート保存データが不正です。")

    raw_fields = draft.get("fields") or []
    if not isinstance(raw_fields, list):
        raise BridgeError("template.invalid_format", "テンプレート項目の形式が不正です。")

    fields = [_field_from_draft(item) for item in raw_fields if isinstance(item, dict)]
    template_name = str(draft.get("template_name") or "ocr-template")
    sample_image_size = draft.get("sample_image_size")
    if not isinstance(sample_image_size, dict):
        sample_image_size = None

    data = build_template_data(
        fields=fields,
        lang=str(draft.get("lang") or "jpn+eng"),
        tesseract_path=str(draft.get("tesseract_path") or ""),
        sample_image=str(draft.get("sample_image") or ""),
        output_settings=draft.get("output_settings") if isinstance(draft.get("output_settings"), dict) else {},
        template_name=template_name,
        sample_image_size=sample_image_size,
    )

    save_path = payload.get("save_path")
    path = Path(str(save_path)) if save_path else Path.cwd() / f"{_safe_file_stem(template_name)}.json"
    save_template(path, data)
    return {"path": str(path), "template": data, "draft": _template_to_draft(data)}


def template_load(payload: dict[str, Any]) -> dict[str, Any]:
    path_value = payload.get("path") or "ocr-template.json"
    path = Path(str(path_value))
    if not path.exists():
        raise BridgeError("file.not_found", "テンプレートファイルが見つかりません。", {"path": str(path)})

    data = load_template(path)
    if not isinstance(data, dict):
        raise BridgeError("template.invalid_format", "テンプレートファイルの形式が不正です。")

    return {"path": str(path), "template": data, "draft": _template_to_draft(data)}


COMMANDS = {
    "template_save": template_save,
    "template_load": template_load,
}


def _read_request() -> dict[str, Any]:
    raw_bytes = sys.stdin.buffer.read()
    if raw_bytes.count(b"\x00") > len(raw_bytes) // 4:
        raw = raw_bytes.decode("utf-16", errors="strict")
    else:
        raw = raw_bytes.decode("utf-8-sig", errors="strict")
    if not raw.strip():
        return {}
    try:
        request = json.loads(raw)
    except json.JSONDecodeError as error:
        raise BridgeError("bridge.invalid_json", "ブリッジ入力の JSON が不正です。", {"error": str(error)}) from error
    if not isinstance(request, dict):
        raise BridgeError("bridge.invalid_json", "ブリッジ入力は JSON object である必要があります。")
    payload = request.get("payload", request)
    if not isinstance(payload, dict):
        raise BridgeError("bridge.invalid_json", "ブリッジ payload は JSON object である必要があります。")
    return payload


def _write_response(response: dict[str, Any]) -> None:
    raw = json.dumps(response, ensure_ascii=False)
    sys.stdout.buffer.write(raw.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def main() -> int:
    if len(sys.argv) < 2:
        response = {
            "ok": False,
            "error": {
                "code": "bridge.missing_command",
                "message": "ブリッジコマンドが指定されていません。",
                "details": {},
            },
        }
        _write_response(response)
        return 2

    command_name = sys.argv[1]
    command = COMMANDS.get(command_name)
    if command is None:
        response = {
            "ok": False,
            "error": {
                "code": "bridge.unknown_command",
                "message": f"未対応のブリッジコマンドです: {command_name}",
                "details": {},
            },
        }
        _write_response(response)
        return 2

    try:
        data = command(_read_request())
        _write_response({"ok": True, "data": data})
        return 0
    except BridgeError as error:
        _write_response(
            {
                "ok": False,
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "details": error.details,
                },
            }
        )
        return 1
    except Exception as error:
        _write_response(
            {
                "ok": False,
                "error": {
                    "code": "bridge.process_failed",
                    "message": "Python ブリッジでエラーが発生しました。",
                    "details": {"error": str(error)},
                },
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
