from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class ApplicationCopy:
    workflow_choice_note: str = (
        "画像フォルダを選び、読み取り範囲を新規テンプレートで指定します。"
    )
    auto_detection_action: str = "読み取り範囲を自動検出"
    detection_statuses: Mapping[str, str] = field(default_factory=dict)
    detection_fallback_status: str = "自動検出結果を確認してください。"
    detection_failure_status: str = (
        "読み取り範囲を自動検出できませんでした。範囲を確認してください。"
    )
    fixed_ocr_backend_label: str = "標準（PaddleOCR）"


@dataclass(frozen=True)
class ApplicationConfig:
    app_id: str
    app_title: str
    data_directory_name: str
    default_template_name: str = "ocr-template.json"
    launcher_subtitle: str = "実行環境ランチャー"
    copy: ApplicationCopy = field(default_factory=ApplicationCopy)

