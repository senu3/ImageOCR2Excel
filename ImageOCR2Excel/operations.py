from __future__ import annotations

from collections.abc import Callable


class OperationCancelled(Exception):
    """Raised when a background operation reaches a safe cancellation point."""


CancelCheck = Callable[[], bool]


def raise_if_cancelled(cancel_check: CancelCheck | None) -> None:
    if cancel_check is not None and cancel_check():
        raise OperationCancelled("処理がキャンセルされました。")

