from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ImageOCR2Excel.export.excel import ExportResult


STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_EXCLUDED = "excluded"


@dataclass
class ImageQueueItem:
    path: Path
    included: bool = True
    status: str = STATUS_PENDING
    detail: str = ""
    resume_status: str = STATUS_PENDING


class ImageQueue:
    def __init__(self) -> None:
        self.items: list[ImageQueueItem] = []

    def reset(self, paths: list[Path]) -> None:
        self.items = [ImageQueueItem(path) for path in paths]

    def included_files(self) -> list[Path]:
        return [item.path for item in self.items if item.included]

    def failed_files(self) -> list[Path]:
        return [item.path for item in self.items if item.included and item.status == STATUS_FAILED]

    def set_included(self, path: Path, included: bool) -> None:
        item = self._find(path)
        if item is None:
            return
        if included and not item.included:
            item.included = True
            item.status = item.resume_status
            return
        if not included and item.included:
            item.included = False
            item.resume_status = item.status
            item.status = STATUS_EXCLUDED

    def set_all(self, included: bool) -> None:
        for item in self.items:
            self.set_included(item.path, included)

    def prepare(self, paths: list[Path]) -> None:
        targets = set(paths)
        for item in self.items:
            if item.path in targets:
                item.status = STATUS_PENDING
                item.resume_status = STATUS_PENDING
                item.detail = ""

    def mark_processing(self, path: Path) -> None:
        item = self._find(path)
        if item is not None:
            item.status = STATUS_PROCESSING
            item.resume_status = STATUS_PROCESSING
            item.detail = ""

    def apply_result(self, result: "ExportResult") -> None:
        for record in result.records:
            item = self._find(record.image_path)
            if item is None:
                continue
            result_status = STATUS_FAILED if record.errors else STATUS_SUCCESS
            item.resume_status = result_status
            if item.included:
                item.status = result_status
            item.detail = record.errors[0].error if record.errors else ""

    def _find(self, path: Path) -> ImageQueueItem | None:
        for item in self.items:
            if item.path == path:
                return item
        return None

