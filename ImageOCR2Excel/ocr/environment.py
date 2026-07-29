from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable, Mapping, MutableMapping

from ImageOCR2Excel.persistence import atomic_write_text


OCR_ENV_READY = "ready"
OCR_ENV_VERIFY = "verify"
OCR_ENV_SETUP = "setup"
OCR_ENV_LOCATION_ERROR = "location_error"
OCR_ENV_UNAVAILABLE = "unavailable"

_SETTINGS_VERSION = 2
_MODEL_MARKER_NAMES = {
    "inference.json",
    "inference.pdiparams",
    "inference.pdmodel",
    "model.pdiparams",
    "model.pdmodel",
}


@dataclass(frozen=True)
class OcrEnvironmentStatus:
    state: str
    label: str
    detail: str
    cache_dir: Path
    cache_mode: str

    @property
    def ready(self) -> bool:
        return self.state == OCR_ENV_READY


@dataclass(frozen=True)
class OcrEnvironmentSettings:
    cache_mode: str = "auto"
    custom_cache_dir: str = ""
    verified_signature: str = ""
    verified_models: tuple[tuple[str, int], ...] = ()


class OcrEnvironmentManager:
    """Own machine-local PaddleOCR cache settings and reusable readiness checks."""

    def __init__(
        self,
        *,
        settings_path: Path | None = None,
        app_directory_name: str = "ImageOCR2Excel",
        home: Path | None = None,
        environ: MutableMapping[str, str] | None = None,
        dependency_checker: Callable[[str], bool] | None = None,
        version_provider: Callable[[str], str] | None = None,
    ) -> None:
        self.home = Path(home) if home is not None else Path.home()
        self.environ = environ if environ is not None else os.environ
        self.settings_path = settings_path or self._default_settings_path(
            self.environ, self.home, app_directory_name
        )
        self._dependency_checker = dependency_checker or self._has_package
        self._version_provider = version_provider or self._package_version
        self.settings = self._load()

    @staticmethod
    def _default_settings_path(
        environ: Mapping[str, str],
        home: Path,
        app_directory_name: str = "ImageOCR2Excel",
    ) -> Path:
        local_app_data = environ.get("LOCALAPPDATA", "").strip()
        base = Path(local_app_data) if local_app_data else home / "AppData" / "Local"
        return base / app_directory_name / "settings.json"

    @staticmethod
    def _has_package(name: str) -> bool:
        return importlib.util.find_spec(name) is not None

    @staticmethod
    def _package_version(name: str) -> str:
        try:
            return version(name)
        except PackageNotFoundError:
            return "missing"

    def _load(self) -> OcrEnvironmentSettings:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return OcrEnvironmentSettings()
        if not isinstance(data, dict) or data.get("version") not in {1, 2}:
            return OcrEnvironmentSettings()
        mode = "custom" if data.get("ocr_cache_mode") == "custom" else "auto"
        custom = str(data.get("ocr_cache_dir") or "").strip()
        if mode == "custom" and not custom:
            mode = "auto"
        if data.get("version") == 1:
            # Version 1 only remembered that *some* model file existed. Preserve
            # the cache choice, but require one explicit verification before the
            # environment can be considered ready again.
            return OcrEnvironmentSettings(mode, custom)
        verified = str(data.get("ocr_verified_signature") or "").strip()
        models: list[tuple[str, int]] = []
        raw_models = data.get("ocr_verified_models")
        if isinstance(raw_models, list):
            for raw_model in raw_models:
                if not isinstance(raw_model, dict):
                    continue
                relative_path = str(raw_model.get("path") or "").strip()
                size = raw_model.get("size")
                candidate = Path(relative_path)
                if (
                    not relative_path
                    or candidate.is_absolute()
                    or ".." in candidate.parts
                    or not isinstance(size, int)
                    or size < 0
                ):
                    continue
                models.append((candidate.as_posix(), size))
        return OcrEnvironmentSettings(mode, custom, verified, tuple(models))

    def _save(self) -> None:
        data = {
            "version": _SETTINGS_VERSION,
            "ocr_cache_mode": self.settings.cache_mode,
            "ocr_cache_dir": self.settings.custom_cache_dir,
            "ocr_verified_signature": self.settings.verified_signature,
            "ocr_verified_models": [
                {"path": relative_path, "size": size}
                for relative_path, size in self.settings.verified_models
            ],
        }
        atomic_write_text(
            self.settings_path,
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        )

    @property
    def cache_dir(self) -> Path:
        if self.settings.cache_mode == "custom":
            return Path(self.settings.custom_cache_dir).expanduser()
        return self.home / ".paddlex"

    @property
    def cache_mode_label(self) -> str:
        return "指定フォルダー" if self.settings.cache_mode == "custom" else "自動管理"

    def apply_to_process(self) -> Path:
        cache_dir = self.cache_dir
        self.environ["PADDLE_PDX_CACHE_HOME"] = str(cache_dir)
        return cache_dir

    def use_custom_cache(self, path: Path) -> None:
        target = Path(path).expanduser().resolve()
        if not target.exists() or not target.is_dir():
            raise ValueError("選択した保存先フォルダーを確認できません。")
        self.settings = OcrEnvironmentSettings("custom", str(target), "")
        self.apply_to_process()
        self._save()

    def use_automatic_cache(self) -> None:
        self.settings = OcrEnvironmentSettings("auto", "", "")
        self.apply_to_process()
        self._save()

    def invalidate_verification(self) -> None:
        if (
            not self.settings.verified_signature
            and not self.settings.verified_models
        ):
            return
        self.settings = OcrEnvironmentSettings(
            self.settings.cache_mode, self.settings.custom_cache_dir, ""
        )
        self._save()

    def quick_status(self) -> OcrEnvironmentStatus:
        cache_dir = self.cache_dir
        if not self._dependencies_available():
            return OcrEnvironmentStatus(
                OCR_ENV_UNAVAILABLE,
                "ランチャーで修復が必要",
                "PaddleOCRの実行環境が不足しています。ランチャーで修復してください。",
                cache_dir,
                self.settings.cache_mode,
            )
        location_error = self._location_error(cache_dir)
        if location_error:
            return OcrEnvironmentStatus(
                OCR_ENV_LOCATION_ERROR,
                "保存先を確認",
                location_error,
                cache_dir,
                self.settings.cache_mode,
            )
        has_models = self._has_model_files(cache_dir)
        if not self.settings.verified_models:
            if has_models:
                return OcrEnvironmentStatus(
                    OCR_ENV_VERIFY,
                    "確認が必要",
                    "既存の認識モデルはまだこのアプリで確認されていません。",
                    cache_dir,
                    self.settings.cache_mode,
                )
            return OcrEnvironmentStatus(
                OCR_ENV_SETUP,
                "認識モデルが必要",
                "通信環境により時間がかかる場合があります。",
                cache_dir,
                self.settings.cache_mode,
            )
        if not self._verified_models_available(cache_dir):
            return OcrEnvironmentStatus(
                OCR_ENV_SETUP,
                "再準備が必要",
                "確認済みの認識モデルが不足または変更されています。",
                cache_dir,
                self.settings.cache_mode,
            )
        if self.settings.verified_signature != self._signature(cache_dir):
            return OcrEnvironmentStatus(
                OCR_ENV_VERIFY,
                "確認が必要",
                "OCR実行環境が更新されています。認識モデルを再確認してください。",
                cache_dir,
                self.settings.cache_mode,
            )
        return OcrEnvironmentStatus(
            OCR_ENV_READY,
            "準備完了",
            "必要な認識モデルを確認しました。",
            cache_dir,
            self.settings.cache_mode,
        )

    def verify(self, verifier: Callable[[], None]) -> OcrEnvironmentStatus:
        """Run a full probe and record the exact model files it verified."""
        self.apply_to_process()
        self.invalidate_verification()
        verifier()
        return self.record_verified()

    def record_verified(self) -> OcrEnvironmentStatus:
        """Record models after a successful probe performed in another process."""
        models = self._model_inventory(self.cache_dir)
        if not models:
            raise RuntimeError("認識モデルの保存を確認できませんでした。")
        self.settings = OcrEnvironmentSettings(
            self.settings.cache_mode,
            self.settings.custom_cache_dir,
            self._signature(self.cache_dir),
            models,
        )
        self._save()
        status = self.quick_status()
        if not status.ready:
            self.invalidate_verification()
            raise RuntimeError("認識モデルの準備完了を確認できませんでした。")
        return status

    def _dependencies_available(self) -> bool:
        return all(
            self._dependency_checker(package) for package in ("paddle", "paddleocr")
        )

    def _location_error(self, cache_dir: Path) -> str:
        if self.settings.cache_mode == "custom" and not cache_dir.exists():
            return "指定した保存先が見つかりません。"
        if cache_dir.exists() and not cache_dir.is_dir():
            return "モデル保存先がフォルダーではありません。"
        existing = cache_dir
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        if not existing.exists() or not os.access(existing, os.R_OK | os.W_OK):
            return "モデル保存先を読み書きできません。"
        return ""

    @staticmethod
    def _has_model_files(cache_dir: Path) -> bool:
        model_root = cache_dir / "official_models"
        if not model_root.is_dir():
            return False
        try:
            return any(
                path.is_file()
                and (path.name in _MODEL_MARKER_NAMES or path.suffix == ".pdiparams")
                for path in model_root.rglob("*")
            )
        except OSError:
            return False

    @staticmethod
    def _model_inventory(cache_dir: Path) -> tuple[tuple[str, int], ...]:
        model_root = cache_dir / "official_models"
        if not model_root.is_dir():
            return ()
        models: list[tuple[str, int]] = []
        try:
            for path in model_root.rglob("*"):
                if not path.is_file() or not (
                    path.name in _MODEL_MARKER_NAMES
                    or path.suffix == ".pdiparams"
                ):
                    continue
                models.append(
                    (path.relative_to(cache_dir).as_posix(), path.stat().st_size)
                )
        except OSError:
            return ()
        return tuple(sorted(models))

    def _verified_models_available(self, cache_dir: Path) -> bool:
        try:
            for relative_path, expected_size in self.settings.verified_models:
                path = cache_dir / Path(relative_path)
                if not path.is_file() or path.stat().st_size != expected_size:
                    return False
        except OSError:
            return False
        return True

    def _signature(self, cache_dir: Path) -> str:
        return "|".join(
            (
                str(cache_dir.resolve()).casefold(),
                self._version_provider("paddleocr"),
                self._version_provider("paddlepaddle"),
            )
        )

