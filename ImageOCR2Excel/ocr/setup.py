from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ImageOCR2Excel.operations import OperationCancelled


EVENT_PREFIX = "OCR_SETUP "


@dataclass(frozen=True)
class OcrSetupPhase:
    name: str
    message: str


class OcrSetupProcessRunner:
    """Run PaddleOCR model initialization in a cancellable child process."""

    def __init__(self, python_executable: str | None = None) -> None:
        self.python_executable = python_executable or sys.executable

    def run(
        self,
        *,
        language: str,
        cache_dir: Path,
        cancel_check: Callable[[], bool] | None = None,
        on_phase: Callable[[OcrSetupPhase], None] | None = None,
    ) -> None:
        environment = os.environ.copy()
        environment["PADDLE_PDX_CACHE_HOME"] = str(cache_dir)
        creation_flags = 0
        if sys.platform.startswith("win"):
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            [
                self.python_executable,
                "-m",
                "ImageOCR2Excel.ocr.setup_worker",
                "--language",
                language,
                "--cache-dir",
                str(cache_dir),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=environment,
            creationflags=creation_flags,
        )
        output_queue: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    output_queue.put(line.rstrip())
            finally:
                output_queue.put(None)

        threading.Thread(target=read_output, daemon=True).start()
        output_finished = False
        last_output = ""
        try:
            while not output_finished or process.poll() is None:
                if cancel_check is not None and cancel_check():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    raise OperationCancelled()
                try:
                    line = output_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if line is None:
                    output_finished = True
                    continue
                if line:
                    last_output = line
                phase = self._parse_phase(line)
                if phase is not None and on_phase is not None:
                    on_phase(phase)
            return_code = process.wait()
            if return_code != 0:
                message = self._error_message(last_output)
                raise RuntimeError(message)
        finally:
            if process.poll() is None:
                process.terminate()

    @staticmethod
    def _parse_phase(line: str) -> OcrSetupPhase | None:
        if not line.startswith(EVENT_PREFIX):
            return None
        try:
            payload = json.loads(line[len(EVENT_PREFIX) :])
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("type") != "phase":
            return None
        name = str(payload.get("name") or "").strip()
        message = str(payload.get("message") or "").strip()
        if not name or not message:
            return None
        return OcrSetupPhase(name, message)

    @staticmethod
    def _error_message(last_output: str) -> str:
        if last_output.startswith(EVENT_PREFIX):
            try:
                payload = json.loads(last_output[len(EVENT_PREFIX) :])
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict) and payload.get("type") == "error":
                message = str(payload.get("message") or "").strip()
                if message:
                    return message
        if last_output:
            return f"認識モデルの準備処理が終了しました: {last_output}"
        return "認識モデルの準備処理が予期せず終了しました。"

