from __future__ import annotations

import re
import shutil
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from ocr_models import POSTPROCESS_OPTIONS, TemplateField

try:
    import pytesseract
except ImportError:
    pytesseract = None


DEFAULT_LANG = "jpn+eng"


def detect_tesseract() -> str:
    found = shutil.which("tesseract")
    if found:
        return found
    default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    return str(default) if default.exists() else ""


def fill_missing_field_source_size(fields: list[TemplateField], image_size: tuple[int, int]) -> None:
    width, height = image_size
    for field in fields:
        if not field.source_width or not field.source_height:
            field.source_width = width
            field.source_height = height


def scaled_field(field: TemplateField, image: Image.Image | None) -> TemplateField:
    region = field.normalized()
    if image is None or not region.source_width or not region.source_height:
        return region
    target_width, target_height = image.size
    scale_x = target_width / region.source_width
    scale_y = target_height / region.source_height
    x1 = round(region.x1 * scale_x)
    y1 = round(region.y1 * scale_y)
    x2 = round(region.x2 * scale_x)
    y2 = round(region.y2 * scale_y)
    x1, x2 = sorted((max(0, min(target_width, x1)), max(0, min(target_width, x2))))
    y1, y2 = sorted((max(0, min(target_height, y1)), max(0, min(target_height, y2))))
    return TemplateField(
        region.name,
        x1,
        y1,
        x2,
        y2,
        region.enabled,
        target_width,
        target_height,
        region.postprocess,
        region.replace_from,
        region.replace_to,
        region.remove_text,
    )


def prepare_for_ocr(image: Image.Image) -> Image.Image:
    scale = 3 if max(image.size) < 500 else 2
    image = image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
    image = ImageOps.grayscale(image)
    image = ImageEnhance.Contrast(image).enhance(1.8)
    image = image.filter(ImageFilter.SHARPEN)
    return image


def clean_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return " ".join(line for line in lines if line).strip()


def apply_postprocess(text: str, field: TemplateField) -> str:
    value = text
    if field.replace_from:
        value = value.replace(field.replace_from, field.replace_to)
    for token in [part.strip() for part in field.remove_text.split(",") if part.strip()]:
        value = value.replace(token, "")
    value = " ".join(value.split()).strip()

    if field.postprocess == "数字のみ":
        return re.sub(r"\D+", "", value)
    if field.postprocess == "数値抽出":
        match = re.search(r"[-+]?\d+(?:[.,]\d+)?", value)
        return match.group(0).replace(",", ".") if match else ""
    if field.postprocess == "英数字のみ":
        return re.sub(r"[^0-9A-Za-z]+", "", value)
    return value


class OcrEngine:
    def recognize_field(
        self,
        image: Image.Image,
        field: TemplateField,
        lang: str = DEFAULT_LANG,
        tesseract_path: str = "",
    ) -> tuple[str | None, str | None]:
        if pytesseract is None:
            return None, "OCRライブラリ pytesseract がインストールされていません。"

        if tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path

        region = scaled_field(field, image)
        if (region.x2 - region.x1) < 1 or (region.y2 - region.y1) < 1:
            return None, "読み取り範囲が画像外、または小さすぎます。"

        crop = image.crop((region.x1, region.y1, region.x2, region.y2))
        prepared = prepare_for_ocr(crop)
        try:
            text = pytesseract.image_to_string(prepared, lang=lang.strip() or DEFAULT_LANG, config="--oem 3 --psm 6")
        except pytesseract.TesseractNotFoundError:
            return None, "Tesseractが見つかりません。設定で実行ファイルのパスを指定してください。"
        except pytesseract.TesseractError as exc:
            return None, str(exc)
        return apply_postprocess(clean_text(text), field), None
