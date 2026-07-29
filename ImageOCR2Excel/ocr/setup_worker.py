from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ImageOCR2Excel.ocr.engine import OcrEngine
from ImageOCR2Excel.ocr.setup import EVENT_PREFIX


PHASE_MESSAGES = {
    "initialize": "モデル保存先を確認しています。",
    "recognize": "通常認識モデルを確認しています。不足している場合は取得します。",
    "recognize_lines": "行認識モデルを確認しています。不足している場合は取得します。",
    "complete": "認識モデルの動作確認が完了しました。",
}


def emit(event_type: str, **payload: str) -> None:
    print(
        EVENT_PREFIX
        + json.dumps({"type": event_type, **payload}, ensure_ascii=False),
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--language", required=True)
    parser.add_argument("--cache-dir", required=True, type=Path)
    args = parser.parse_args()
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(args.cache_dir)
    emit("phase", name="initialize", message=PHASE_MESSAGES["initialize"])
    try:
        OcrEngine().verify_paddle_environment(
            args.language,
            on_phase=lambda name: emit(
                "phase", name=name, message=PHASE_MESSAGES[name]
            ),
        )
    except Exception as error:
        emit("error", message=str(error) or type(error).__name__)
        return 1
    emit("phase", name="complete", message=PHASE_MESSAGES["complete"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

