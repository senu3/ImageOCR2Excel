from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Protocol


class WorkbookLike(Protocol):
    def save(self, filename: str | Path) -> None: ...


@contextmanager
def atomic_output_path(path: Path) -> Iterator[Path]:
    """Yield a same-directory temporary path and replace the destination on success."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}-",
        suffix=path.suffix or ".tmp",
        dir=path.parent,
    )
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        yield temp_path
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_write_text(path: Path, value: str, encoding: str = "utf-8") -> None:
    with atomic_output_path(path) as temp_path:
        with temp_path.open("w", encoding=encoding, newline="") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())


def atomic_save_workbook(workbook: WorkbookLike, path: Path) -> None:
    with atomic_output_path(path) as temp_path:
        workbook.save(temp_path)
        with temp_path.open("r+b") as handle:
            os.fsync(handle.fileno())

