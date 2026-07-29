from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol, TypedDict

from PIL import Image

from ImageOCR2Excel.models import (
    CoordinateSettings,
    SetDefinition,
    SetDetectionResult,
    TemplateField,
    TextFormattingSettings,
    find_set_definition,
    text_formatting_settings_from_dict,
)


class ImageTextCorrector(Protocol):
    def __call__(
        self,
        crop: Image.Image,
        field: TemplateField,
        text: str,
        reference_width: int,
        /,
    ) -> str: ...


class CoordinateSettingsValues(TypedDict, total=False):
    coordinate_space: str
    source_content_rect: tuple[int, int, int, int] | None
    black_threshold: int
    min_nonblack_ratio: float
    stable_run: int
    aspect_ratio_tolerance: float


@dataclass(frozen=True)
class FieldPreset:
    family: str
    prefixes: tuple[str, ...]
    ocr: Mapping[str, object]

    def matches(self, field_name: str) -> bool:
        return any(field_name.startswith(prefix) for prefix in self.prefixes)


@dataclass(frozen=True)
class AutoDetectionField:
    name: str
    slot_key: str
    ocr_line_split: str = "none"


@dataclass(frozen=True)
class AutoDetectionStrategy:
    detect: Callable[
        [Image.Image, list[TemplateField], CoordinateSettings],
        SetDetectionResult,
    ]
    resolve_sets: Callable[
        [Image.Image, list[TemplateField], CoordinateSettings],
        SetDetectionResult,
    ]


@dataclass(frozen=True)
class OcrProfile:
    profile_id: str
    name: str
    default_backend: str
    default_lang: str
    coordinate_defaults: CoordinateSettingsValues
    default_field_ocr: Mapping[str, object]
    text_format_defaults: Mapping[str, object] = field(default_factory=dict)
    field_presets: tuple[FieldPreset, ...] = field(default_factory=tuple)
    set_definitions: tuple[SetDefinition, ...] = field(default_factory=tuple)
    default_set_preset: str = ""
    auto_detection_field_specs: tuple[AutoDetectionField, ...] = field(
        default_factory=tuple
    )
    auto_detection: AutoDetectionStrategy | None = None
    image_text_fallback: ImageTextCorrector | None = None
    image_text_corrector: ImageTextCorrector | None = None

    def coordinate_settings(self) -> CoordinateSettings:
        return CoordinateSettings(**self.coordinate_defaults).normalized()

    def text_formatting_settings(self) -> TextFormattingSettings:
        return text_formatting_settings_from_dict(dict(self.text_format_defaults))

    def field_family(self, field_name: str) -> str:
        preset = self._matching_preset(field_name)
        return preset.family if preset else field_name

    def has_field_ocr_preset(self, field_name: str) -> bool:
        return self._matching_preset(field_name) is not None

    def field_ocr_preset(self, field_name: str) -> dict:
        values = dict(self.default_field_ocr)
        preset = self._matching_preset(field_name)
        if preset:
            values.update(preset.ocr)
        return values

    def auto_detection_fields(self) -> list[TemplateField]:
        fields = [
            TemplateField(
                spec.name,
                0,
                0,
                1,
                1,
                set_id=1,
                slot_key=spec.slot_key,
                ocr_line_split=spec.ocr_line_split,
            )
            for spec in self.auto_detection_field_specs
        ]
        for field in fields:
            for key, value in self.field_ocr_preset(field.name).items():
                setattr(field, key, value)
        return fields

    @property
    def default_set_definition(self) -> SetDefinition:
        if not self.default_set_preset:
            raise ValueError(
                f"OCRプロファイル '{self.profile_id}' に既定のセット定義がありません。"
            )
        return self.set_definition(self.default_set_preset)

    def set_definition(self, preset: str) -> SetDefinition:
        return find_set_definition(self.set_definitions, preset)

    @property
    def set_preset_display(self) -> dict[str, str]:
        return {
            definition.preset: definition.name
            for definition in self.set_definitions
        }

    def _matching_preset(self, field_name: str) -> FieldPreset | None:
        return next(
            (
                preset
                for preset in self.field_presets
                if preset.matches(field_name)
            ),
            None,
        )

