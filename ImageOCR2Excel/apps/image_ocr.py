from __future__ import annotations

from pathlib import Path

from ImageOCR2Excel.app_config import ApplicationConfig, ApplicationCopy
from ImageOCR2Excel.profiles import get_profile


PROFILE = get_profile("generic")
CONFIG = ApplicationConfig(
    app_id="ImageOCR2Excel",
    app_title="ImageOCR2Excel",
    data_directory_name="ImageOCR2Excel",
    default_template_name="image-ocr-template.json",
    copy=ApplicationCopy(
        workflow_choice_note=(
            "固定フォーマット画像の読み取り範囲を指定し、"
            "同じ形式の画像フォルダをExcelへ出力します。"
        ),
        detection_failure_status=(
            "このアプリでは自動検出を使用しません。新規テンプレートで範囲を指定してください。"
        ),
        fixed_ocr_backend_label="標準（PaddleOCR）",
    ),
)


def main() -> None:
    from ImageOCR2Excel.application import main as run_application

    run_application(PROFILE, CONFIG)


def launcher_main(
    argv: list[str] | None = None,
    *,
    project_root: Path | None = None,
) -> int:
    from ImageOCR2Excel.launcher.ui import main as run_launcher

    return run_launcher(argv, project_root=project_root, app_config=CONFIG)
