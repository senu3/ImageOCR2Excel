from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import warnings
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, cast

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

from ImageOCR2Excel.models import COORDINATE_SPACE_CONTENT, CORRECTION_ALL_TARGET, LINE_JOIN_FULLWIDTH_SPACE, LINE_JOIN_NEWLINE, CoordinateSettings, CorrectionRule, TemplateField, TextFormattingSettings


FALLBACK_LANG = "eng"
FALLBACK_BACKEND = "paddle"
_PADDLE_IMPORT_LOCK = threading.Lock()


class _quiet_paddle_import:
    """Hide Windows `where ccache` noise emitted during Paddle's lazy import."""

    def __enter__(self) -> None:
        _PADDLE_IMPORT_LOCK.acquire()
        self._check_output = subprocess.check_output

        def quiet_check_output(*args: Any, **kwargs: Any) -> Any:
            kwargs.setdefault("stderr", subprocess.DEVNULL)
            return self._check_output(*args, **kwargs)

        subprocess.check_output = quiet_check_output
        self._warnings = warnings.catch_warnings()
        self._warnings.__enter__()
        warnings.filterwarnings("ignore", message="No ccache found.*")

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._warnings.__exit__(exc_type, exc, traceback)
        subprocess.check_output = self._check_output
        _PADDLE_IMPORT_LOCK.release()


def fill_missing_field_source_size(fields: list[TemplateField], image_size: tuple[int, int]) -> None:
    width, height = image_size
    for field in fields:
        if not field.source_width or not field.source_height:
            field.source_width = width
            field.source_height = height


def _stable_edge(values: list[int], cutoff: int, stable_run: int, reverse: bool = False) -> int | None:
    indexes = range(len(values) - stable_run, -1, -1) if reverse else range(0, len(values) - stable_run + 1)
    for index in indexes:
        if all(values[index + offset] >= cutoff for offset in range(stable_run)):
            return index + stable_run if reverse else index
    return None


def _pixel_values(image: Image.Image) -> list[int]:
    flattened = getattr(image, "get_flattened_data", None)
    data = flattened() if callable(flattened) else image.getdata()
    return cast(list[int], list(cast(Any, data)))


def detect_content_rect(image: Image.Image, settings: CoordinateSettings) -> tuple[int, int, int, int] | None:
    """Detect stable non-black edges around a letterboxed game viewport."""
    normalized = settings.normalized()
    gray = ImageOps.grayscale(image)
    mask = gray.point([0 if value <= normalized.black_threshold else 255 for value in range(256)])
    row_values = _pixel_values(mask.resize((1, image.height), Image.Resampling.BOX))
    col_values = _pixel_values(mask.resize((image.width, 1), Image.Resampling.BOX))
    cutoff = max(1, round(255 * normalized.min_nonblack_ratio))
    top = _stable_edge(row_values, cutoff, normalized.stable_run)
    bottom = _stable_edge(row_values, cutoff, normalized.stable_run, reverse=True)
    left = _stable_edge(col_values, cutoff, normalized.stable_run)
    right = _stable_edge(col_values, cutoff, normalized.stable_run, reverse=True)
    if left is None or top is None or right is None or bottom is None:
        return None
    rect = (left, top, right, bottom)
    if rect[2] - rect[0] < image.width * 0.5 or rect[3] - rect[1] < image.height * 0.5:
        return None
    source_rect = normalized.source_content_rect
    if source_rect:
        source_ratio = (source_rect[2] - source_rect[0]) / max(1, source_rect[3] - source_rect[1])
        target_ratio = (rect[2] - rect[0]) / max(1, rect[3] - rect[1])
        if abs(target_ratio / source_ratio - 1.0) > normalized.aspect_ratio_tolerance:
            return None
    return rect


def scaled_field(
    field: TemplateField,
    image: Image.Image | None,
    coordinate_settings: CoordinateSettings | None = None,
    target_content_rect: tuple[int, int, int, int] | None = None,
) -> TemplateField:
    region = field.normalized()
    if image is None or not region.source_width or not region.source_height:
        return region
    target_width, target_height = image.size
    settings = (coordinate_settings or CoordinateSettings()).normalized()
    source_rect = settings.source_content_rect
    if settings.coordinate_space == COORDINATE_SPACE_CONTENT and source_rect:
        target_rect = target_content_rect or detect_content_rect(image, settings)
    else:
        target_rect = None
    if target_rect:
        assert source_rect is not None
        source_x1, source_y1, source_x2, source_y2 = source_rect
        target_x1, target_y1, target_x2, target_y2 = target_rect
        scale_x = (target_x2 - target_x1) / (source_x2 - source_x1)
        scale_y = (target_y2 - target_y1) / (source_y2 - source_y1)
        x1 = round(target_x1 + (region.x1 - source_x1) * scale_x)
        y1 = round(target_y1 + (region.y1 - source_y1) * scale_y)
        x2 = round(target_x1 + (region.x2 - source_x1) * scale_x)
        y2 = round(target_y1 + (region.y2 - source_y1) * scale_y)
    else:
        scale_x = target_width / region.source_width
        scale_y = target_height / region.source_height
        x1 = round(region.x1 * scale_x)
        y1 = round(region.y1 * scale_y)
        x2 = round(region.x2 * scale_x)
        y2 = round(region.y2 * scale_y)
    x1, x2 = sorted((max(0, min(target_width, x1)), max(0, min(target_width, x2))))
    y1, y2 = sorted((max(0, min(target_height, y1)), max(0, min(target_height, y2))))
    return replace(
        region,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        source_width=target_width,
        source_height=target_height,
    )


def prepare_for_ocr(
    image: Image.Image,
    preprocess: str = "default",
    scale: str = "auto",
    threshold: int = 180,
) -> Image.Image:
    if preprocess == "outline-threshold":
        image = crop_dark_content(image, threshold=120, margin=20)
        image = ImageOps.expand(image, border=30, fill="white")
    elif preprocess == "white-glyph":
        return prepare_white_glyph(image, scale, threshold)

    scale_value = 3 if scale == "auto" and max(image.size) < 500 else 2 if scale == "auto" else int(scale)
    image = image.resize((image.width * scale_value, image.height * scale_value), Image.Resampling.LANCZOS)
    image = ImageOps.grayscale(image)
    if preprocess == "gray":
        return image

    image = ImageEnhance.Contrast(image).enhance(1.8)
    image = image.filter(ImageFilter.SHARPEN)
    if preprocess == "default":
        return image

    thresholded = image.point(lambda value: 255 if value >= threshold else 0)
    if preprocess == "invert-threshold":
        return ImageOps.invert(thresholded)
    return thresholded


def prepare_white_glyph(image: Image.Image, scale: str = "auto", threshold: int = 190) -> Image.Image:
    gray = ImageOps.grayscale(image)
    min_near = gray.filter(ImageFilter.MinFilter(5))
    bright_mask = gray.point(lambda value: 255 if value >= threshold else 0)
    dark_near = min_near.point(lambda value: 255 if value <= 100 else 0)
    glyph = ImageChops.multiply(bright_mask, dark_near)
    image = ImageOps.invert(glyph)
    image = crop_black_content(image, margin=12)
    image = ImageOps.expand(image, border=24, fill="white")
    scale_value = 3 if scale == "auto" and max(image.size) < 500 else 2 if scale == "auto" else int(scale)
    return image.resize((image.width * scale_value, image.height * scale_value), Image.Resampling.LANCZOS)


def crop_dark_content(image: Image.Image, threshold: int, margin: int) -> Image.Image:
    gray = ImageOps.grayscale(image)
    pixels = gray.load()
    assert pixels is not None
    xs: list[int] = []
    ys: list[int] = []
    for y in range(gray.height):
        for x in range(gray.width):
            if cast(int, pixels[x, y]) < threshold:
                xs.append(x)
                ys.append(y)

    if not xs:
        return image

    box = (
        max(0, min(xs) - margin),
        max(0, min(ys) - margin),
        min(image.width, max(xs) + margin),
        min(image.height, max(ys) + margin),
    )
    return image.crop(box)


def crop_black_content(image: Image.Image, margin: int) -> Image.Image:
    bbox = image.point(lambda value: 255 if value < 128 else 0).getbbox()
    if bbox is None:
        return image
    left, top, right, bottom = bbox
    box = (
        max(0, left - margin),
        max(0, top - margin),
        min(image.width, right + margin),
        min(image.height, bottom + margin),
    )
    return image.crop(box)


def clean_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return " ".join(line for line in lines if line).strip()


def fullwidth_ascii(text: str) -> str:
    converted: list[str] = []
    for char in text:
        codepoint = ord(char)
        if char == " ":
            converted.append("　")
        elif 0x21 <= codepoint <= 0x7E:
            converted.append(chr(codepoint + 0xFEE0))
        else:
            converted.append(char)
    return "".join(converted)


def format_ocr_text(
    text: str,
    settings: TextFormattingSettings | None,
) -> str:
    normalized = (settings or TextFormattingSettings()).normalized()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    separator = {
        LINE_JOIN_FULLWIDTH_SPACE: "　",
        LINE_JOIN_NEWLINE: "\n",
    }.get(normalized.line_join, "")
    value = separator.join(lines)
    return fullwidth_ascii(value) if normalized.fullwidth_ascii else value


def detect_line_bands(
    image: Image.Image,
    threshold: int = 90,
    min_ratio: float = 0.015,
    gap: int = 10,
    padding: int = 12,
) -> list[tuple[int, int]]:
    gray = ImageOps.grayscale(image)
    pixels = gray.load()
    assert pixels is not None
    min_count = max(3, int(image.width * min_ratio))
    rows: list[int] = []
    for y in range(gray.height):
        count = 0
        for x in range(gray.width):
            if cast(int, pixels[x, y]) < threshold:
                count += 1
        if count >= min_count:
            rows.append(y)

    if not rows:
        return [(0, image.height)]

    bands: list[tuple[int, int]] = []
    start = previous = rows[0]
    for y in rows[1:]:
        if y - previous <= gap:
            previous = y
            continue
        add_detected_band(bands, image.height, start, previous, padding)
        start = previous = y
    add_detected_band(bands, image.height, start, previous, padding)
    return bands or [(0, image.height)]


def add_detected_band(bands: list[tuple[int, int]], height: int, start: int, end: int, padding: int) -> None:
    if end - start <= 8:
        return
    bands.append((max(0, start - padding), min(height, end + padding)))


def apply_postprocess(
    text: str,
    field: TemplateField,
    preserve_whitespace: bool = False,
) -> str:
    value = text
    if field.replace_from:
        value = value.replace(field.replace_from, field.replace_to)
    for token in [part.strip() for part in field.remove_text.split(",") if part.strip()]:
        value = value.replace(token, "")
    value = value.strip() if preserve_whitespace else " ".join(value.split()).strip()

    if field.postprocess == "数字のみ":
        return re.sub(r"\D+", "", value)
    if field.postprocess == "数値抽出":
        match = re.search(r"[-+]?\d+(?:[.,]\d+)?", value)
        return match.group(0).replace(",", ".") if match else ""
    if field.postprocess == "英数字のみ":
        return re.sub(r"[^0-9A-Za-z]+", "", value)
    return value


def apply_correction_rules(text: str, field: TemplateField, correction_rules: list[CorrectionRule] | None = None) -> str:
    value = text
    for rule in correction_rules or []:
        pattern = rule.pattern
        if not rule.enabled or not pattern:
            continue
        if rule.target not in {CORRECTION_ALL_TARGET, field.name}:
            continue
        value = value.replace(pattern, rule.replacement)
    return value


def resolve_backend(field: TemplateField, backend: str = FALLBACK_BACKEND) -> str:
    field_backend = getattr(field, "ocr_backend", "default") or "default"
    if field_backend != "default":
        return field_backend
    return backend if backend in {"paddle"} else FALLBACK_BACKEND


def paddle_lang(lang: str) -> str:
    value = (lang or "").lower()
    if "jpn" in value or "japan" in value:
        return "japan"
    if "eng" in value or value == "en":
        return "en"
    return "japan"


def extract_paddle_texts(result: Any) -> list[str]:
    if result is None:
        return []
    if isinstance(result, str):
        return [result] if result.strip() else []
    if isinstance(result, dict):
        texts: list[str] = []
        found_text_key = False
        for key in ("rec_text", "rec_texts", "text", "texts", "label"):
            if key in result:
                found_text_key = True
                texts.extend(extract_paddle_texts(result[key]))
        if found_text_key:
            return texts
        if not texts:
            for value in result.values():
                texts.extend(extract_paddle_texts(value))
        return texts
    if isinstance(result, (list, tuple)):
        if len(result) == 2 and isinstance(result[0], str) and isinstance(result[1], (float, int)):
            return [result[0]]
        texts: list[str] = []
        for value in result:
            texts.extend(extract_paddle_texts(value))
        return texts
    for attr in ("to_dict", "json"):
        method = getattr(result, attr, None)
        if callable(method):
            try:
                return extract_paddle_texts(method())
            except Exception:
                pass
    return []


class PaddleBackend:
    def __init__(self) -> None:
        self.instances: dict[str, Any] = {}
        self.recognition_instances: dict[str, Any] = {}

    def recognize(self, image: Image.Image, lang: str) -> str:
        ocr = self._instance(lang)
        result = self._run_ocr(ocr, image)
        return "\n".join(extract_paddle_texts(result)).strip()

    def recognize_lines(self, images: list[Image.Image], lang: str) -> str:
        if not images:
            return ""
        recognition = self._recognition_instance(lang)
        result = self._run_text_recognition(recognition, images)
        return " ".join(text.strip() for text in extract_paddle_texts(result) if text.strip()).strip()

    def _instance(self, lang: str) -> Any:
        paddle_language = paddle_lang(lang)
        if paddle_language in self.instances:
            return self.instances[paddle_language]
        try:
            os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "0")
            with _quiet_paddle_import():
                from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError(
                "PaddleOCRを読み込めません。アプリの再インストールまたはREADMEの実行方法を確認してください。"
            ) from exc

        candidates = [
            {
                "lang": paddle_language,
                "use_doc_orientation_classify": False,
                "use_doc_unwarping": False,
                "use_textline_orientation": False,
            },
            {"lang": paddle_language, "use_angle_cls": False, "show_log": False},
            {"lang": paddle_language, "use_angle_cls": False},
            {"lang": paddle_language},
        ]
        last_error: Exception | None = None
        for kwargs in candidates:
            try:
                with _quiet_paddle_import():
                    instance = PaddleOCR(**kwargs)
                self.instances[paddle_language] = instance
                return instance
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"PaddleOCRの初期化に失敗しました: {last_error}")

    def _recognition_instance(self, lang: str) -> Any:
        key = paddle_lang(lang)
        if key in self.recognition_instances:
            return self.recognition_instances[key]
        try:
            os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "0")
            with _quiet_paddle_import():
                from paddleocr import TextRecognition
        except ImportError as exc:
            raise RuntimeError(
                "PaddleOCRを読み込めません。アプリの再インストールまたはREADMEの実行方法を確認してください。"
            ) from exc

        candidates = [
            {"model_name": "PP-OCRv6_medium_rec"},
            {},
        ]
        last_error: Exception | None = None
        for kwargs in candidates:
            try:
                with _quiet_paddle_import():
                    instance = TextRecognition(**kwargs)
                self.recognition_instances[key] = instance
                return instance
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"PaddleOCRの認識モデル初期化に失敗しました: {last_error}")

    def _run_ocr(self, ocr: Any, image: Image.Image) -> Any:
        image = image.convert("RGB")
        predict_method = getattr(ocr, "predict", None)
        if callable(predict_method):
            try:
                import numpy as np

                return predict_method(
                    np.array(image),
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                )
            except (ImportError, TypeError):
                pass

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "ocr_crop.png"
            image.save(image_path)
            if callable(predict_method):
                try:
                    return predict_method(
                        str(image_path),
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        use_textline_orientation=False,
                    )
                except TypeError:
                    try:
                        return predict_method(input=str(image_path))
                    except TypeError:
                        return predict_method(str(image_path))
            ocr_method = getattr(ocr, "ocr", None)
            if callable(ocr_method):
                for kwargs in ({"det": False, "cls": False}, {"det": False}, {}):
                    try:
                        return ocr_method(str(image_path), **kwargs)
                    except TypeError:
                        continue
        return None

    def _run_text_recognition(self, recognition: Any, images: list[Image.Image]) -> Any:
        import numpy as np

        inputs = [np.array(image.convert("RGB")) for image in images]
        predict_method = getattr(recognition, "predict", None)
        if not callable(predict_method):
            raise RuntimeError("PaddleOCRの認識モデルを実行できません。")
        try:
            return predict_method(input=inputs, batch_size=max(1, len(inputs)))
        except TypeError:
            return predict_method(inputs, batch_size=max(1, len(inputs)))


class OcrEngine:
    def __init__(
        self,
        image_text_corrector: Callable[
            [Image.Image, TemplateField, str, int], str
        ]
        | None = None,
        image_text_fallback: Callable[
            [Image.Image, TemplateField, str, int], str
        ]
        | None = None,
    ) -> None:
        self.paddle_backend: PaddleBackend | None = None
        self.image_text_corrector = image_text_corrector
        self.image_text_fallback = image_text_fallback
        self._content_rect_image: Image.Image | None = None
        self._content_rect_settings: CoordinateSettings | None = None
        self._content_rect_value: tuple[int, int, int, int] | None = None

    def verify_paddle_environment(
        self,
        lang: str = FALLBACK_LANG,
        on_phase: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize both PaddleOCR paths used by normal and line-split fields."""
        backend = PaddleBackend()
        sample = Image.new("RGB", (320, 96), "white")
        if on_phase is not None:
            on_phase("recognize")
        backend.recognize(sample, lang)
        if on_phase is not None:
            on_phase("recognize_lines")
        backend.recognize_lines([sample], lang)
        self.paddle_backend = backend

    def content_rect_for(self, image: Image.Image, settings: CoordinateSettings | None) -> tuple[int, int, int, int] | None:
        normalized = (settings or CoordinateSettings()).normalized()
        if normalized.coordinate_space != COORDINATE_SPACE_CONTENT:
            return None
        if self._content_rect_image is image and self._content_rect_settings == normalized:
            return self._content_rect_value
        self._content_rect_image = image
        self._content_rect_settings = normalized
        self._content_rect_value = detect_content_rect(image, normalized)
        return self._content_rect_value

    def recognize_field_detail(
        self,
        image: Image.Image,
        field: TemplateField,
        lang: str = FALLBACK_LANG,
        backend_config: str = "",
        backend: str = FALLBACK_BACKEND,
        correction_rules: list[CorrectionRule] | None = None,
        coordinate_settings: CoordinateSettings | None = None,
        text_formatting: TextFormattingSettings | None = None,
    ) -> tuple[str | None, str | None, str | None]:
        del backend_config
        backend_name = resolve_backend(field, backend)
        if backend_name != "paddle":
            return None, None, f"未対応のOCRバックエンドです: {backend_name}"

        target_content_rect = self.content_rect_for(image, coordinate_settings)
        region = scaled_field(field, image, coordinate_settings, target_content_rect)
        if (region.x2 - region.x1) < 1 or (region.y2 - region.y1) < 1:
            return None, None, "読み取り範囲が画像外、または小さすぎます。"

        crop = image.crop((region.x1, region.y1, region.x2, region.y2))
        if region.known_empty:
            return "", "", None
        ocr_lang = field.ocr_lang.strip() or lang.strip() or FALLBACK_LANG
        psm = field.ocr_psm or 6
        config = f"--oem 3 --psm {psm}"
        try:
            if field.ocr_line_split == "detected":
                raw_text = self.recognize_detected_lines(crop, field, ocr_lang, config, backend_name)
            else:
                prepared = prepare_for_ocr(crop, field.ocr_preprocess, field.ocr_scale, field.ocr_threshold)
                raw_text = self.recognize_prepared(prepared, ocr_lang, config, backend_name)
        except RuntimeError as exc:
            return None, None, str(exc)
        except Exception as exc:
            return None, None, f"PaddleOCRエラー: {exc}"

        if self.image_text_fallback is not None or self.image_text_corrector is not None:
            reference_width = (
                target_content_rect[2] - target_content_rect[0]
                if target_content_rect is not None
                else image.width
            )
        if self.image_text_fallback is not None:
            raw_text = self.image_text_fallback(
                crop,
                field,
                raw_text,
                reference_width,
            )
        if self.image_text_corrector is not None:
            raw_text = self.image_text_corrector(
                crop,
                region,
                raw_text,
                reference_width,
            )

        cleaned = clean_text(raw_text)
        formatted = format_ocr_text(raw_text, text_formatting)
        corrected = apply_correction_rules(formatted, field, correction_rules)
        raw_display = raw_text.strip() if field.ocr_line_split == "detected" else cleaned
        return raw_display, apply_postprocess(
            corrected,
            field,
            preserve_whitespace=True,
        ), None

    def recognize_prepared(self, image: Image.Image, lang: str, config: str, backend: str) -> str:
        if backend == "paddle":
            if self.paddle_backend is None:
                self.paddle_backend = PaddleBackend()
            return self.paddle_backend.recognize(image, lang)
        raise RuntimeError(f"未対応のOCRバックエンドです: {backend}")

    def recognize_detected_lines(self, crop: Image.Image, field: TemplateField, lang: str, config: str, backend: str) -> str:
        # PaddleOCR has its own text detector. The recognition-only model loses
        # text when the simple dark-pixel row detector sees only part of a line.
        if backend == "paddle":
            prepared = prepare_for_ocr(crop, field.ocr_preprocess, field.ocr_scale, field.ocr_threshold)
            return self.recognize_prepared(prepared, lang, config, backend)

        bands = detect_line_bands(
            crop,
            field.ocr_line_detect_threshold,
            field.ocr_line_detect_min_ratio,
            field.ocr_line_detect_gap,
            field.ocr_line_padding,
        )
        prepared_lines: list[Image.Image] = []
        for y1, y2 in bands:
            line_crop = crop.crop((0, y1, crop.width, y2))
            prepared = prepare_for_ocr(line_crop, field.ocr_preprocess, field.ocr_scale, field.ocr_threshold)
            prepared_lines.append(prepared)

        parts: list[str] = []
        for prepared in prepared_lines:
            text = self.recognize_prepared(prepared, lang, config, backend)
            cleaned = clean_text(text)
            if cleaned:
                parts.append(cleaned)
        return "\n".join(parts)

    def recognize_field(
        self,
        image: Image.Image,
        field: TemplateField,
        lang: str = FALLBACK_LANG,
        backend_config: str = "",
        backend: str = FALLBACK_BACKEND,
        correction_rules: list[CorrectionRule] | None = None,
        coordinate_settings: CoordinateSettings | None = None,
        text_formatting: TextFormattingSettings | None = None,
    ) -> tuple[str | None, str | None]:
        _raw_text, value, error = self.recognize_field_detail(image, field, lang, backend_config, backend, correction_rules, coordinate_settings, text_formatting)
        return value, error

