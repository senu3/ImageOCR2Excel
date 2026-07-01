from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path
from typing import Any

from excel_exporter import ExcelExporter, validate_export_settings
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


def image_open(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from PIL import Image
    except ImportError as error:
        raise BridgeError("image.unsupported_format", "画像処理ライブラリ Pillow がインストールされていません。") from error

    path_value = payload.get("path")
    if not path_value:
        raise BridgeError("file.not_found", "画像ファイルが指定されていません。")

    path = Path(str(path_value))
    if not path.exists():
        raise BridgeError("file.not_found", "画像ファイルが見つかりません。", {"path": str(path)})

    try:
        with Image.open(path) as image:
            width, height = image.size
    except Exception as error:
        raise BridgeError("image.unsupported_format", "画像ファイルを開けませんでした。", {"path": str(path), "error": str(error)}) from error

    return {
        "path": str(path),
        "name": path.name,
        "width": width,
        "height": height,
    }


def ocr_preview(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from PIL import Image
    except ImportError as error:
        raise BridgeError("image.unsupported_format", "画像処理ライブラリ Pillow がインストールされていません。") from error

    from ocr_engine import DEFAULT_LANG, OcrEngine, detect_tesseract

    image_path_value = payload.get("image_path")
    if not image_path_value:
        raise BridgeError("file.not_found", "OCR対象の画像が指定されていません。")

    image_path = Path(str(image_path_value))
    if not image_path.exists():
        raise BridgeError("file.not_found", "OCR対象の画像が見つかりません。", {"path": str(image_path)})

    draft = payload.get("draft")
    template = payload.get("template")
    source = draft if isinstance(draft, dict) else template if isinstance(template, dict) else None
    if source is None:
        raise BridgeError("template.invalid_format", "OCR対象のテンプレートデータが不正です。")

    raw_fields = source.get("fields") or []
    if not isinstance(raw_fields, list):
        raise BridgeError("template.invalid_format", "テンプレート項目の形式が不正です。")

    requested_ids = payload.get("field_ids")
    field_ids = {str(field_id) for field_id in requested_ids} if isinstance(requested_ids, list) else set()
    fields: list[tuple[str, TemplateField]] = []
    for index, item in enumerate(raw_fields):
        if not isinstance(item, dict):
            continue
        field_id = str(item.get("id") or f"field-{index + 1}")
        if field_ids and field_id not in field_ids:
            continue
        field = _field_from_draft(item)
        if not field.enabled and not field_ids:
            continue
        fields.append((field_id, field))

    if not fields:
        raise BridgeError("template.invalid_field", "OCR対象の項目がありません。")

    try:
        image = Image.open(image_path).convert("RGB")
    except Exception as error:
        raise BridgeError("image.unsupported_format", "OCR対象の画像を開けませんでした。", {"path": str(image_path), "error": str(error)}) from error

    lang = str(source.get("lang") or DEFAULT_LANG)
    tesseract_path = str(source.get("tesseract_path") or "") or detect_tesseract()
    engine = OcrEngine()
    results = []
    for field_id, field in fields:
        raw_text, value, error = engine.recognize_field_detail(image, field, lang, tesseract_path)
        results.append(
            {
                "field_id": field_id,
                "name": field.name,
                "raw_text": raw_text or "",
                "value": value or "",
                "error": error,
                "warnings": [],
            }
        )

    return {"image_path": str(image_path), "results": results}


def export_excel(payload: dict[str, Any]) -> dict[str, Any]:
    from ocr_engine import DEFAULT_LANG, OcrEngine, detect_tesseract

    image_path_value = payload.get("image_path")
    output_path_value = payload.get("output_path")
    if not image_path_value:
        raise BridgeError("file.not_found", "Excel出力対象の画像が指定されていません。")
    if not output_path_value:
        raise BridgeError("file.not_found", "Excel出力先が指定されていません。")

    image_path = Path(str(image_path_value))
    output_path = Path(str(output_path_value))
    if not image_path.exists():
        raise BridgeError("file.not_found", "Excel出力対象の画像が見つかりません。", {"path": str(image_path)})

    draft = payload.get("draft")
    template = payload.get("template")
    source = draft if isinstance(draft, dict) else template if isinstance(template, dict) else None
    if source is None:
        raise BridgeError("template.invalid_format", "Excel出力対象のテンプレートデータが不正です。")

    raw_fields = source.get("fields") or []
    if not isinstance(raw_fields, list):
        raise BridgeError("template.invalid_format", "テンプレート項目の形式が不正です。")

    field_pairs: list[tuple[str, TemplateField]] = []
    for index, item in enumerate(raw_fields):
        if not isinstance(item, dict):
            continue
        field = _field_from_draft(item)
        if not field.enabled:
            continue
        field_pairs.append((str(item.get("id") or f"field-{index + 1}"), field))

    if not field_pairs:
        raise BridgeError("template.invalid_field", "Excel出力対象の項目がありません。")

    output_settings = payload.get("output_settings")
    if not isinstance(output_settings, dict):
        output_settings = source.get("output_settings") if isinstance(source.get("output_settings"), dict) else {}

    write_mode = str(output_settings.get("write_mode") or "overwrite")
    settings, error = validate_export_settings(
        sheet_name=str(output_settings.get("sheet_name") or "OCR結果"),
        write_mode="追記" if write_mode in {"append", "追記"} else "上書き",
        start_cell=str(output_settings.get("start_cell") or "A1"),
        include_filename=bool(output_settings.get("include_filename", True)),
        include_header=bool(output_settings.get("include_header", True)),
    )
    if error or settings is None:
        raise BridgeError("excel.invalid_settings", error or "Excel出力設定が不正です。")

    review_results = payload.get("review_results")
    reviewed_values: dict[str, str] = {}
    if isinstance(review_results, list):
        for item in review_results:
            if not isinstance(item, dict) or item.get("error"):
                continue
            field_id = item.get("field_id") or item.get("fieldId")
            if field_id is None:
                continue
            reviewed_values[str(field_id)] = str(item.get("value") or "")

    reviewed_value_queue: deque[str | None] = deque(reviewed_values.get(field_id) for field_id, _ in field_pairs)
    lang = str(source.get("lang") or DEFAULT_LANG)
    tesseract_path = str(source.get("tesseract_path") or "") or detect_tesseract()
    engine = OcrEngine()

    def ocr_callback(image, field: TemplateField) -> tuple[str | None, str | None]:
        reviewed_value = reviewed_value_queue.popleft() if reviewed_value_queue else None
        if reviewed_value is not None:
            return reviewed_value, None
        return engine.recognize_field(image, field, lang, tesseract_path)

    try:
        result = ExcelExporter().export(
            output_path=output_path,
            image_files=[image_path],
            fields=[field for _, field in field_pairs],
            settings=settings,
            ocr_callback=ocr_callback,
        )
    except Exception as error:
        raise BridgeError("excel.export_failed", "Excel出力に失敗しました。", {"error": str(error)}) from error

    return {
        "path": str(output_path),
        "row_count": result.total_images,
        "total_images": result.total_images,
        "error_count": len(result.errors),
        "errors": [
            {"image": error.image, "field": error.field, "error": error.error}
            for error in result.errors
        ],
    }


COMMANDS = {
    "image_open": image_open,
    "ocr_preview": ocr_preview,
    "template_save": template_save,
    "template_load": template_load,
    "export_excel": export_excel,
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
