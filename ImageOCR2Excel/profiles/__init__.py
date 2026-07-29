from __future__ import annotations

from importlib import import_module

from ImageOCR2Excel.profiles.base import OcrProfile


_PROFILE_LOADERS = {
    "generic": (
        "ImageOCR2Excel.profiles.generic",
        "PROFILE",
    ),
}
_PROFILE_CACHE: dict[str, OcrProfile] = {}


def get_profile(profile_id: str) -> OcrProfile:
    try:
        module_name, attribute = _PROFILE_LOADERS[profile_id]
    except KeyError as exc:
        raise ValueError(f"未登録のOCRプロファイルです: {profile_id}") from exc
    if profile_id not in _PROFILE_CACHE:
        profile = getattr(import_module(module_name), attribute)
        if not isinstance(profile, OcrProfile):
            raise TypeError(
                f"OCRプロファイル '{profile_id}' の形式が不正です。"
            )
        _PROFILE_CACHE[profile_id] = profile
    return _PROFILE_CACHE[profile_id]


def registered_profiles() -> tuple[OcrProfile, ...]:
    return tuple(get_profile(profile_id) for profile_id in _PROFILE_LOADERS)

