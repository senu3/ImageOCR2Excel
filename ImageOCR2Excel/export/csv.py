from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path
from typing import Callable

from ImageOCR2Excel.export.excel import (
    ExcelExporter,
    ExportError,
    ExportNotice,
    ExportResult,
    ExportSettings,
    ImageExportRecord,
    OcrCallback,
    SetResolver,
    resolve_export_set_definition,
)
from ImageOCR2Excel.models import (
    SetDefinition,
    TemplateField,
)
from ImageOCR2Excel.operations import CancelCheck, raise_if_cancelled
from ImageOCR2Excel.persistence import atomic_write_text


class CsvExporter:
    """Write the same recognized rows as Excel export to a UTF-8 BOM CSV."""

    def __init__(self, row_exporter: ExcelExporter | None = None) -> None:
        self.row_exporter = row_exporter or ExcelExporter()

    def export(
        self,
        output_path: Path,
        image_files: list[Path],
        fields: list[TemplateField],
        settings: ExportSettings,
        ocr_callback: OcrCallback,
        progress_callback: Callable[[int, int, Path], None] | None = None,
        set_definition: SetDefinition | None = None,
        set_resolver: SetResolver | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> ExportResult:
        raise_if_cancelled(cancel_check)
        definition = resolve_export_set_definition(settings, set_definition)
        first_data_row = 2 if settings.include_header else 1
        rows, result = self.row_exporter.recognize_rows(
            image_files,
            fields,
            settings,
            ocr_callback,
            start_row=first_data_row,
            progress_callback=progress_callback,
            set_definition=definition,
            set_resolver=set_resolver,
            cancel_check=cancel_check,
        )

        output = StringIO(newline="")
        writer = csv.writer(output, lineterminator="\r\n")
        if settings.include_header:
            writer.writerow(
                self.row_exporter.headers(
                    fields,
                    settings,
                    definition,
                )
            )
        writer.writerows(rows)

        raise_if_cancelled(cancel_check)
        atomic_write_text(output_path, output.getvalue(), encoding="utf-8-sig")
        return result

    def retry_failed(
        self,
        output_path: Path,
        previous_result: ExportResult,
        fields: list[TemplateField],
        settings: ExportSettings,
        ocr_callback: OcrCallback,
        retry_files: list[Path] | None = None,
        progress_callback: Callable[[int, int, Path], None] | None = None,
        set_definition: SetDefinition | None = None,
        set_resolver: SetResolver | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> ExportResult:
        raise_if_cancelled(cancel_check)
        if not output_path.exists():
            raise FileNotFoundError(f"再実行するCSVファイルがありません: {output_path}")

        with output_path.open("r", encoding="utf-8-sig", newline="") as handle:
            output_rows = list(csv.reader(handle))

        definition = resolve_export_set_definition(settings, set_definition)
        if settings.include_header:
            expected_headers = self.row_exporter.headers(
                fields,
                settings,
                definition,
            )
            if not output_rows or output_rows[0] != expected_headers:
                raise ValueError(
                    "CSVのヘッダーが前回の出力と一致しません。"
                    "全画像を再出力してください。"
                )
        retry_targets = set(retry_files) if retry_files is not None else None
        retry_records = [
            record
            for record in previous_result.failed_records
            if retry_targets is None or record.image_path in retry_targets
        ]
        retry_paths = {record.image_path for record in retry_records}
        errors: list[ExportError] = [
            error
            for record in previous_result.records
            if record.image_path not in retry_paths
            for error in record.errors
        ]
        notices: list[ExportNotice] = [
            notice
            for record in previous_result.records
            if record.image_path not in retry_paths
            for notice in record.notices
        ]
        updated_by_path: dict[Path, ImageExportRecord] = {}
        total = len(retry_records)

        for row_index, record in enumerate(retry_records, start=1):
            raise_if_cancelled(cancel_check)
            rows, image_errors, image_notices = (
                self.row_exporter.recognize_image_rows(
                    record.image_path,
                    fields,
                    settings,
                    ocr_callback,
                    definition,
                    set_resolver,
                    cancel_check,
                )
            )
            if len(rows) != record.row_count:
                raise ValueError(
                    "出力レイアウトが前回の一括出力から変更されています。"
                    "全画像を再出力してください。"
                )
            start_index = record.start_row - 1
            end_index = start_index + record.row_count
            if start_index < 0 or end_index > len(output_rows):
                raise ValueError(
                    "前回出力した行をCSV内で確認できません。"
                    "全画像を再出力してください。"
                )
            output_rows[start_index:end_index] = rows
            errors.extend(image_errors)
            notices.extend(image_notices)
            updated_by_path[record.image_path] = ImageExportRecord(
                record.image_path,
                record.start_row,
                record.row_count,
                image_errors,
                image_notices,
            )
            if progress_callback:
                progress_callback(row_index, total, record.image_path)

        output = StringIO(newline="")
        writer = csv.writer(output, lineterminator="\r\n")
        writer.writerows(output_rows)
        raise_if_cancelled(cancel_check)
        atomic_write_text(output_path, output.getvalue(), encoding="utf-8-sig")
        records = [
            updated_by_path.get(record.image_path, record)
            for record in previous_result.records
        ]
        return ExportResult(previous_result.total_images, errors, records, notices)

