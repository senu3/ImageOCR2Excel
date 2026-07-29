from __future__ import annotations

from dataclasses import asdict, dataclass, field


TEMPLATE_VERSION = 1
POSTPROCESS_OPTIONS = ["そのまま", "数字のみ", "数値抽出", "英数字のみ"]
OCR_BACKEND_OPTIONS = ["default", "paddle"]
OCR_PREPROCESS_OPTIONS = ["default", "gray", "threshold", "invert-threshold", "outline-threshold", "white-glyph"]
OCR_SCALE_OPTIONS = ["auto", "2", "3", "4"]
OCR_LINE_SPLIT_OPTIONS = ["none", "detected"]
LINE_JOIN_NONE = "none"
LINE_JOIN_FULLWIDTH_SPACE = "fullwidth_space"
LINE_JOIN_NEWLINE = "newline"
LINE_JOIN_OPTIONS = [LINE_JOIN_NONE, LINE_JOIN_FULLWIDTH_SPACE, LINE_JOIN_NEWLINE]
CORRECTION_ALL_TARGET = "すべて"
EXPORT_LAYOUT_IMAGE_ROW = "画像ごとに1行"
EXPORT_LAYOUT_SET = "セットごとに1行"
EXPORT_LAYOUT_OPTIONS = [EXPORT_LAYOUT_IMAGE_ROW, EXPORT_LAYOUT_SET]
COORDINATE_SPACE_IMAGE = "image"
COORDINATE_SPACE_CONTENT = "content"
COORDINATE_SPACE_OPTIONS = [COORDINATE_SPACE_IMAGE, COORDINATE_SPACE_CONTENT]


@dataclass(frozen=True)
class SetColumn:
    key: str
    label: str
    ocr_line_split: str = "none"

    def normalized(self) -> "SetColumn":
        key = self.key.strip()
        label = self.label.strip()
        return SetColumn(
            key=key,
            label=label,
            ocr_line_split=(
                self.ocr_line_split
                if self.ocr_line_split in OCR_LINE_SPLIT_OPTIONS
                else "none"
            ),
        )

    def to_dict(self) -> dict:
        return asdict(self.normalized())


@dataclass(frozen=True)
class SetDefinition:
    preset: str
    name: str
    order_label: str
    columns: tuple[SetColumn, ...]
    extra_slots: tuple[SetColumn, ...] = ()
    additional_rows: tuple[tuple[str, ...], ...] = ()

    def normalized(self) -> "SetDefinition":
        columns = tuple(column.normalized() for column in self.columns)
        extra_slots = tuple(column.normalized() for column in self.extra_slots)
        valid_keys = {column.key for column in (*columns, *extra_slots)}
        additional_rows = tuple(
            tuple(key.strip() for key in row)
            for row in self.additional_rows
            if len(row) == len(columns)
            and all(key.strip() in valid_keys for key in row)
        )
        return SetDefinition(
            preset=self.preset.strip() or "default",
            name=self.name.strip() or "セット",
            order_label=self.order_label.strip() or "順序",
            columns=columns,
            extra_slots=extra_slots,
            additional_rows=additional_rows,
        )

    @property
    def slot_keys(self) -> tuple[str, ...]:
        return tuple(column.key for column in self.columns)

    def slot_label(self, key: str) -> str:
        return next(
            (
                column.label
                for column in (*self.columns, *self.extra_slots)
                if column.key == key
            ),
            "未割当",
        )

    def column_for(self, key: str) -> SetColumn | None:
        return next(
            (
                column
                for column in (*self.columns, *self.extra_slots)
                if column.key == key
            ),
            None,
        )

    def allowed_slot_keys(self) -> tuple[str, ...]:
        return tuple(column.key for column in (*self.columns, *self.extra_slots))

    def to_dict(self) -> dict:
        normalized = self.normalized()
        return {
            "preset": normalized.preset,
            "name": normalized.name,
            "order_label": normalized.order_label,
            "columns": [column.to_dict() for column in normalized.columns],
            "extra_slots": [
                column.to_dict() for column in normalized.extra_slots
            ],
            "additional_rows": [
                list(row) for row in normalized.additional_rows
            ],
        }


DEFAULT_SET_DEFINITION = SetDefinition(
    "default",
    "項目セット",
    "順序",
    (SetColumn("value", "項目"),),
)


def find_set_definition(
    definitions: tuple[SetDefinition, ...],
    preset: str,
) -> SetDefinition:
    try:
        return next(
            definition.normalized()
            for definition in definitions
            if definition.preset == preset
        )
    except StopIteration as exc:
        raise ValueError(f"未登録のセット定義です: {preset}") from exc


def set_definition_from_dict(
    item: dict | None,
    fallback: SetDefinition | None = None,
) -> SetDefinition:
    data = item or {}
    default = (fallback or DEFAULT_SET_DEFINITION).normalized()
    preset = str(data.get("preset") or default.preset)
    columns_data = data.get("columns")
    if not isinstance(columns_data, list) or not columns_data:
        return default
    columns = tuple(
        SetColumn(
            str(column.get("key") or ""),
            str(column.get("label") or ""),
            str(column.get("ocr_line_split") or "none"),
        )
        for column in columns_data
        if isinstance(column, dict)
    )
    extra_slots_data = data.get("extra_slots", [])
    extra_slots = tuple(
        SetColumn(
            str(column.get("key") or ""),
            str(column.get("label") or ""),
            str(column.get("ocr_line_split") or "none"),
        )
        for column in extra_slots_data
        if isinstance(column, dict)
    ) if isinstance(extra_slots_data, list) else ()
    additional_rows_data = data.get("additional_rows", [])
    additional_rows = tuple(
        tuple(str(key) for key in row)
        for row in additional_rows_data
        if isinstance(row, list)
    ) if isinstance(additional_rows_data, list) else ()
    return SetDefinition(
        preset,
        str(data.get("name") or default.name),
        str(data.get("order_label") or default.order_label),
        columns or default.columns,
        extra_slots if "extra_slots" in data else default.extra_slots,
        additional_rows
        if "additional_rows" in data
        else default.additional_rows,
    ).normalized()


@dataclass(frozen=True)
class CoordinateSettings:
    coordinate_space: str = COORDINATE_SPACE_IMAGE
    source_content_rect: tuple[int, int, int, int] | None = None
    black_threshold: int = 8
    min_nonblack_ratio: float = 0.01
    stable_run: int = 6
    aspect_ratio_tolerance: float = 0.08

    def normalized(self) -> "CoordinateSettings":
        coordinate_space = self.coordinate_space if self.coordinate_space in COORDINATE_SPACE_OPTIONS else COORDINATE_SPACE_IMAGE
        rect = self.source_content_rect
        if rect is not None:
            x1, y1, x2, y2 = (int(value) for value in rect)
            rect = (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else None
        return CoordinateSettings(
            coordinate_space=coordinate_space,
            source_content_rect=rect,
            black_threshold=max(0, min(254, int(self.black_threshold))),
            min_nonblack_ratio=max(0.001, min(0.5, float(self.min_nonblack_ratio))),
            stable_run=max(1, min(50, int(self.stable_run))),
            aspect_ratio_tolerance=max(0.0, min(1.0, float(self.aspect_ratio_tolerance))),
        )

    def to_dict(self) -> dict:
        normalized = self.normalized()
        return {
            "coordinate_space": normalized.coordinate_space,
            "source_content_rect": list(normalized.source_content_rect) if normalized.source_content_rect else None,
            "black_threshold": normalized.black_threshold,
            "min_nonblack_ratio": normalized.min_nonblack_ratio,
            "stable_run": normalized.stable_run,
            "aspect_ratio_tolerance": normalized.aspect_ratio_tolerance,
        }


@dataclass(frozen=True)
class TextFormattingSettings:
    line_join: str = LINE_JOIN_FULLWIDTH_SPACE
    fullwidth_ascii: bool = False

    def normalized(self) -> "TextFormattingSettings":
        return TextFormattingSettings(
            line_join=(
                self.line_join
                if self.line_join in LINE_JOIN_OPTIONS
                else LINE_JOIN_FULLWIDTH_SPACE
            ),
            fullwidth_ascii=bool(self.fullwidth_ascii),
        )

    def to_dict(self) -> dict:
        normalized = self.normalized()
        return {
            "line_join": normalized.line_join,
            "fullwidth_ascii": normalized.fullwidth_ascii,
        }


def text_formatting_settings_from_dict(
    item: dict | None,
    fallback: TextFormattingSettings | None = None,
) -> TextFormattingSettings:
    default = (fallback or TextFormattingSettings()).normalized()
    data = item or {}
    return TextFormattingSettings(
        line_join=str(data.get("line_join") or default.line_join),
        fullwidth_ascii=bool(
            data.get("fullwidth_ascii", default.fullwidth_ascii)
        ),
    ).normalized()


def coordinate_settings_from_dict(item: dict | None) -> CoordinateSettings:
    data = item or {}
    rect_data = data.get("source_content_rect")
    rect = tuple(rect_data) if isinstance(rect_data, (list, tuple)) and len(rect_data) == 4 else None
    return CoordinateSettings(
        coordinate_space=str(data.get("coordinate_space") or COORDINATE_SPACE_IMAGE),
        source_content_rect=rect,
        black_threshold=int(data.get("black_threshold", 8)),
        min_nonblack_ratio=float(data.get("min_nonblack_ratio", 0.01)),
        stable_run=int(data.get("stable_run", 6)),
        aspect_ratio_tolerance=float(data.get("aspect_ratio_tolerance", 0.08)),
    ).normalized()


@dataclass
class CorrectionRule:
    pattern: str
    replacement: str = ""
    target: str = CORRECTION_ALL_TARGET
    enabled: bool = True

    def normalized(self) -> "CorrectionRule":
        target = (self.target or CORRECTION_ALL_TARGET).strip() or CORRECTION_ALL_TARGET
        return CorrectionRule(
            self.pattern,
            self.replacement,
            target,
            self.enabled,
        )

    def to_dict(self) -> dict:
        return asdict(self.normalized())


@dataclass(init=False)
class TemplateField:
    name: str
    x1: int
    y1: int
    x2: int
    y2: int
    enabled: bool = True
    source_width: int = 0
    source_height: int = 0
    postprocess: str = "そのまま"
    replace_from: str = ""
    replace_to: str = ""
    remove_text: str = ""
    ocr_backend: str = "default"
    ocr_preprocess: str = "default"
    ocr_scale: str = "auto"
    ocr_threshold: int = 180
    ocr_psm: int = 6
    ocr_lang: str = ""
    ocr_line_split: str = "none"
    ocr_line_padding: int = 12
    ocr_line_detect_threshold: int = 90
    ocr_line_detect_min_ratio: float = 0.015
    ocr_line_detect_gap: int = 10
    set_id: int = 0
    slot_key: str = ""
    known_empty: bool = False

    def __init__(
        self,
        name: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        enabled: bool = True,
        source_width: int = 0,
        source_height: int = 0,
        postprocess: str = "そのまま",
        replace_from: str = "",
        replace_to: str = "",
        remove_text: str = "",
        ocr_backend: str = "default",
        ocr_preprocess: str = "default",
        ocr_scale: str = "auto",
        ocr_threshold: int = 180,
        ocr_psm: int = 6,
        ocr_lang: str = "",
        ocr_line_split: str = "none",
        ocr_line_padding: int = 12,
        ocr_line_detect_threshold: int = 90,
        ocr_line_detect_min_ratio: float = 0.015,
        ocr_line_detect_gap: int = 10,
        set_id: int = 0,
        slot_key: str = "",
        known_empty: bool = False,
    ) -> None:
        self.name = name
        self.x1 = x1
        self.y1 = y1
        self.x2 = x2
        self.y2 = y2
        self.enabled = enabled
        self.source_width = source_width
        self.source_height = source_height
        self.postprocess = postprocess
        self.replace_from = replace_from
        self.replace_to = replace_to
        self.remove_text = remove_text
        self.ocr_backend = ocr_backend
        self.ocr_preprocess = ocr_preprocess
        self.ocr_scale = ocr_scale
        self.ocr_threshold = ocr_threshold
        self.ocr_psm = ocr_psm
        self.ocr_lang = ocr_lang
        self.ocr_line_split = ocr_line_split
        self.ocr_line_padding = ocr_line_padding
        self.ocr_line_detect_threshold = ocr_line_detect_threshold
        self.ocr_line_detect_min_ratio = ocr_line_detect_min_ratio
        self.ocr_line_detect_gap = ocr_line_detect_gap
        self.set_id = set_id
        self.slot_key = slot_key
        self.known_empty = known_empty

    def normalized(self) -> "TemplateField":
        x1, x2 = sorted((self.x1, self.x2))
        y1, y2 = sorted((self.y1, self.y2))
        return TemplateField(
            self.name,
            x1,
            y1,
            x2,
            y2,
            self.enabled,
            self.source_width,
            self.source_height,
            self.postprocess,
            self.replace_from,
            self.replace_to,
            self.remove_text,
            self.ocr_backend,
            self.ocr_preprocess,
            self.ocr_scale,
            self.ocr_threshold,
            self.ocr_psm,
            self.ocr_lang,
            self.ocr_line_split,
            self.ocr_line_padding,
            self.ocr_line_detect_threshold,
            self.ocr_line_detect_min_ratio,
            self.ocr_line_detect_gap,
            max(0, int(self.set_id)),
            self.slot_key.strip(),
            bool(self.known_empty),
        )

    def to_dict(self) -> dict:
        return asdict(self.normalized())


@dataclass(frozen=True)
class SetDetectionResult:
    groups: list[tuple[int, dict[str, TemplateField]]]
    allow_empty_slots: set[str] = field(default_factory=set)
    notices: list[str] = field(default_factory=list)
    review_reason: str = ""
    layout: str = "unknown"
    candidate_fields: list[TemplateField] = field(default_factory=list)


def field_from_dict(item: dict) -> TemplateField:
    postprocess = str(item.get("postprocess") or "そのまま")
    if postprocess not in POSTPROCESS_OPTIONS:
        postprocess = "そのまま"
    ocr_backend = str(item.get("ocr_backend") or "default")
    if ocr_backend not in OCR_BACKEND_OPTIONS:
        ocr_backend = "default"
    ocr_preprocess = str(item.get("ocr_preprocess") or "default")
    if ocr_preprocess not in OCR_PREPROCESS_OPTIONS:
        ocr_preprocess = "default"
    ocr_scale = str(item.get("ocr_scale") or "auto")
    if ocr_scale not in OCR_SCALE_OPTIONS:
        ocr_scale = "auto"
    ocr_line_split = str(item.get("ocr_line_split") or "none")
    if ocr_line_split not in OCR_LINE_SPLIT_OPTIONS:
        ocr_line_split = "none"

    name = str(item["name"])

    return TemplateField(
        name,
        int(item["x1"]),
        int(item["y1"]),
        int(item["x2"]),
        int(item["y2"]),
        bool(item.get("enabled", True)),
        int(item.get("source_width") or 0),
        int(item.get("source_height") or 0),
        postprocess,
        str(item.get("replace_from") or ""),
        str(item.get("replace_to") or ""),
        str(item.get("remove_text") or ""),
        ocr_backend,
        ocr_preprocess,
        ocr_scale,
        int(item.get("ocr_threshold") or 180),
        int(item.get("ocr_psm") or 6),
        str(item.get("ocr_lang") or ""),
        ocr_line_split,
        int(item.get("ocr_line_padding") or 12),
        int(item.get("ocr_line_detect_threshold") or 90),
        float(item.get("ocr_line_detect_min_ratio") or 0.015),
        int(item.get("ocr_line_detect_gap") or 10),
        int(item.get("set_id") or 0),
        str(item.get("slot_key") or ""),
        bool(item.get("known_empty", False)),
    ).normalized()


def set_groups(
    fields: list[TemplateField], definition: SetDefinition
) -> list[tuple[int, dict[str, TemplateField]]]:
    grouped: dict[int, dict[str, TemplateField]] = {}
    for field in fields:
        if not field.enabled or field.set_id <= 0:
            continue
        grouped.setdefault(field.set_id, {})[field.slot_key] = field

    output: list[tuple[int, dict[str, TemplateField]]] = []
    output_order = 1
    for _set_id, slots in sorted(grouped.items()):
        row_keys = (definition.slot_keys, *definition.additional_rows)
        for keys in row_keys:
            if not all(key in slots for key in keys):
                continue
            output.append(
                (
                    output_order,
                    {
                        column.key: slots[source_key]
                        for column, source_key in zip(definition.columns, keys)
                    },
                )
            )
            output_order += 1
    return output


def set_validation_error(
    fields: list[TemplateField], definition: SetDefinition
) -> str | None:
    if not fields:
        labels = "・".join(column.label for column in definition.columns)
        return f"セット出力には、{labels}の範囲が必要です。"
    allowed = set(definition.allowed_slot_keys())
    unassigned = [
        field.name
        for field in fields
        if field.set_id <= 0 or field.slot_key not in allowed
    ]
    if unassigned:
        return f"セットが未設定の項目があります: {', '.join(unassigned)}"
    grouped: dict[int, dict[str, list[str]]] = {}
    for field in fields:
        slots = grouped.setdefault(field.set_id, {})
        slots.setdefault(field.slot_key, []).append(field.name)
    for set_id, slots in sorted(grouped.items()):
        for column in definition.columns:
            names = slots.get(column.key, [])
            if not names:
                return f"セット {set_id} に{column.label}の範囲がありません。"
            if len(names) > 1:
                return f"セット {set_id} に{column.label}の範囲が複数あります: {', '.join(names)}"
        for column in definition.extra_slots:
            names = slots.get(column.key, [])
            if len(names) > 1:
                return (
                    f"セット {set_id} に{column.label}の範囲が複数あります: "
                    f"{', '.join(names)}"
                )
    return None


def correction_rule_from_dict(item: dict) -> CorrectionRule:
    return CorrectionRule(
        str(item.get("pattern") or ""),
        str(item.get("replacement") or ""),
        str(item.get("target") or CORRECTION_ALL_TARGET),
        bool(item.get("enabled", True)),
    ).normalized()

