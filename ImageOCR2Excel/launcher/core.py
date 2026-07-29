from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Mapping, Sequence

from ImageOCR2Excel import __version__
from ImageOCR2Excel.diagnostics import default_app_data_dir


LOGGER_NAME = "ImageOCR2Excel.launcher"
STATE_VERSION = 1
RUNTIME_GENERATION = 1
DEFAULT_PYTHON_VERSION = "3.12"
STATE_FILE_NAME = "launcher-state.json"
LOG_FILE_NAME = "launcher.log"
SIGNATURE_FILES = ("pyproject.toml", "uv.lock", ".python-version")


class LauncherError(RuntimeError):
    """Base error shown by the launcher."""


class LauncherCancelled(LauncherError):
    """Raised after the user cancels environment preparation."""


class LauncherBusy(LauncherError):
    """Raised when another launcher instance owns the setup lock."""


@dataclass(frozen=True)
class LauncherPaths:
    project_root: Path
    app_data_dir: Path
    runtime_dir: Path
    state_path: Path
    log_dir: Path
    log_path: Path
    lock_path: Path

    @property
    def python_executable(self) -> Path:
        if os.name == "nt":
            return self.runtime_dir / "Scripts" / "python.exe"
        return self.runtime_dir / "bin" / "python"


@dataclass(frozen=True)
class LauncherStatus:
    ready: bool
    reason: str
    first_setup: bool
    uv_path: str | None


def _handoff_command(
    project_root: Path,
    *,
    arguments: Sequence[str],
    uv_path: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    environment = dict(os.environ if environ is None else environ)
    executable = uv_path or shutil.which("uv", path=environment.get("PATH"))
    if not executable:
        raise LauncherError(
            "uvが見つかりません。uvをインストールしてターミナルを開き直した後、"
            "配布フォルダー内の起動用 .cmd ファイルを実行してください。"
        )
    launcher_script = Path(project_root).resolve() / "launcher.py"
    if not launcher_script.is_file():
        raise LauncherError("ランチャーが見つかりません。")
    return [
        executable,
        "run",
        "--no-project",
        "--python",
        DEFAULT_PYTHON_VERSION,
        "--",
        "python",
        str(launcher_script),
        *arguments,
    ]


def repair_handoff_command(
    project_root: Path,
    *,
    uv_path: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Build an independent launcher command for an in-app repair request."""
    return _handoff_command(
        project_root,
        arguments=("--repair", "--from-app"),
        uv_path=uv_path,
        environ=environ,
    )


def application_handoff_command(
    project_root: Path,
    *,
    uv_path: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Build an independent launcher command for an in-app restart."""
    return _handoff_command(
        project_root,
        arguments=(),
        uv_path=uv_path,
        environ=environ,
    )


def _launch_handoff(
    project_root: Path,
    *,
    command_builder: Callable[..., list[str]],
    environ: Mapping[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    environment = dict(os.environ if environ is None else environ)
    environment.pop("UV_PROJECT_ENVIRONMENT", None)
    environment.pop("VIRTUAL_ENV", None)
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    command = command_builder(project_root, environ=environment)
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        command,
        cwd=Path(project_root).resolve(),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def launch_repair_handoff(
    project_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """Start the repair launcher without depending on the app runtime."""
    return _launch_handoff(
        project_root,
        command_builder=repair_handoff_command,
        environ=environ,
    )


def launch_application_handoff(
    project_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """Restart the app through the launcher without reusing its runtime."""
    return _launch_handoff(
        project_root,
        command_builder=application_handoff_command,
        environ=environ,
    )


def launcher_paths(
    project_root: Path,
    *,
    app_directory_name: str = "ImageOCR2Excel",
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> LauncherPaths:
    root = Path(project_root).resolve()
    app_data = default_app_data_dir(
        environ,
        home,
        app_directory_name,
    )
    runtime_dir = app_data / "runtime" / f"v{RUNTIME_GENERATION}"
    log_dir = app_data / "logs"
    return LauncherPaths(
        project_root=root,
        app_data_dir=app_data,
        runtime_dir=runtime_dir,
        state_path=app_data / STATE_FILE_NAME,
        log_dir=log_dir,
        log_path=log_dir / LOG_FILE_NAME,
        lock_path=app_data / "launcher.lock",
    )


def project_signature(project_root: Path) -> str:
    digest = hashlib.sha256()
    for name in SIGNATURE_FILES:
        path = Path(project_root) / name
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise LauncherError(f"配布ファイルを読み込めません: {name}") from exc
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def load_state(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        return None
    return data


def save_state(path: Path, signature: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": STATE_VERSION,
        "runtime_generation": RUNTIME_GENERATION,
        "application_version": __version__,
        "python_version": DEFAULT_PYTHON_VERSION,
        "project_signature": signature,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def configure_launcher_logging(paths: LauncherPaths) -> logging.Logger:
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in tuple(logger.handlers):
        if getattr(handler, "_image_launcher_managed", False):
            logger.removeHandler(handler)
            handler.close()
    handler = RotatingFileHandler(
        paths.log_path,
        maxBytes=1 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler._image_launcher_managed = True  # type: ignore[attr-defined]
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    return logger


class SingleInstanceLock:
    """Hold an OS-backed one-byte lock for the lifetime of the launcher."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._stream = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                flock = getattr(fcntl, "flock")
                flock(
                    stream.fileno(),
                    getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB"),
                )
        except OSError as exc:
            stream.close()
            raise LauncherBusy(
                "別のランチャーがセットアップまたは起動を処理しています。"
            ) from exc
        self._stream = stream

    def release(self) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                getattr(fcntl, "flock")(
                    stream.fileno(), getattr(fcntl, "LOCK_UN")
                )
        finally:
            stream.close()
            self._stream = None

    def __enter__(self) -> SingleInstanceLock:
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.release()


class ProcessRunner:
    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._cancel_requested = False

    @property
    def cancel_requested(self) -> bool:
        with self._lock:
            return self._cancel_requested

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        on_output: Callable[[str], None] | None = None,
    ) -> None:
        display_command = subprocess.list2cmdline([str(part) for part in command])
        self.logger.info("Running command | %s", display_command)
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            [str(part) for part in command],
            cwd=cwd,
            env=dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        with self._lock:
            self._process = process
            cancelled = self._cancel_requested
        if cancelled:
            process.terminate()
        output_lines: list[str] = []
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip("\r\n")
            if not line:
                continue
            output_lines.append(line)
            self.logger.info("command | %s", line)
            if on_output is not None:
                on_output(line)
        return_code = process.wait()
        with self._lock:
            self._process = None
            cancelled = self._cancel_requested
        if cancelled:
            raise LauncherCancelled("セットアップを中止しました。")
        if return_code != 0:
            detail = "\n".join(output_lines[-12:]).strip()
            message = f"実行環境の準備に失敗しました（終了コード {return_code}）。"
            if detail:
                message += f"\n\n{detail}"
            raise LauncherError(message)

    def cancel(self) -> None:
        with self._lock:
            self._cancel_requested = True
            process = self._process
        if process is not None and process.poll() is None:
            self.logger.info("Cancellation requested | pid=%s", process.pid)
            try:
                process.terminate()
            except OSError:
                self.logger.exception("Failed to terminate setup process")

    def reset_cancellation(self) -> None:
        with self._lock:
            self._cancel_requested = False


class LauncherService:
    def __init__(
        self,
        paths: LauncherPaths,
        *,
        environ: Mapping[str, str] | None = None,
        uv_path: str | None = None,
        logger: logging.Logger | None = None,
        runner: ProcessRunner | None = None,
    ) -> None:
        self.paths = paths
        self.environ = dict(os.environ if environ is None else environ)
        self.uv_path = uv_path or shutil.which("uv", path=self.environ.get("PATH"))
        self.logger = logger or configure_launcher_logging(paths)
        self.runner = runner or ProcessRunner(self.logger)

    def environment(self) -> dict[str, str]:
        values = dict(self.environ)
        values.pop("VIRTUAL_ENV", None)
        values["UV_PROJECT_ENVIRONMENT"] = str(self.paths.runtime_dir)
        values["PYTHONUTF8"] = "1"
        values["PYTHONIOENCODING"] = "utf-8"
        return values

    def signature(self) -> str:
        return project_signature(self.paths.project_root)

    def status(self, *, force_repair: bool = False) -> LauncherStatus:
        if not self.uv_path:
            return LauncherStatus(False, "uvが見つかりません。", True, None)
        state = load_state(self.paths.state_path)
        first_setup = state is None
        if force_repair:
            return LauncherStatus(False, "実行環境を修復します。", first_setup, self.uv_path)
        if state is None:
            return LauncherStatus(False, "初回セットアップが必要です。", True, self.uv_path)
        if state.get("runtime_generation") != RUNTIME_GENERATION:
            return LauncherStatus(False, "実行環境の更新が必要です。", False, self.uv_path)
        if state.get("project_signature") != self.signature():
            return LauncherStatus(False, "アプリの更新を適用します。", False, self.uv_path)
        if not self.paths.python_executable.is_file():
            return LauncherStatus(False, "実行環境を修復する必要があります。", False, self.uv_path)
        return LauncherStatus(True, "準備完了", False, self.uv_path)

    def sync_command(self, *, reinstall: bool = False) -> list[str]:
        if not self.uv_path:
            raise LauncherError("uvが見つかりません。先にuvをインストールしてください。")
        command = [
            self.uv_path,
            "sync",
            "--project",
            str(self.paths.project_root),
            "--locked",
            "--no-dev",
            "--no-progress",
        ]
        if reinstall:
            command.append("--reinstall")
        return command

    def probe_command(self) -> list[str]:
        if not self.uv_path:
            raise LauncherError("uvが見つかりません。先にuvをインストールしてください。")
        return [
            self.uv_path,
            "run",
            "--project",
            str(self.paths.project_root),
            "--no-sync",
            "--no-dev",
            "--",
            "python",
            "-m",
            "ImageOCR2Excel.launcher.probe",
        ]

    def application_command(self) -> list[str]:
        if not self.uv_path:
            raise LauncherError("uvが見つかりません。先にuvをインストールしてください。")
        python_command = "pythonw" if os.name == "nt" else "python"
        return [
            self.uv_path,
            "run",
            "--project",
            str(self.paths.project_root),
            "--no-sync",
            "--no-dev",
            "--",
            python_command,
            str(self.paths.project_root / "main.py"),
        ]

    def prepare(
        self,
        on_output: Callable[[str], None] | None = None,
        *,
        force_reinstall: bool = False,
    ) -> None:
        self.runner.reset_cancellation()
        self.paths.app_data_dir.mkdir(parents=True, exist_ok=True)
        signature = self.signature()
        self.runner.run(
            self.sync_command(reinstall=force_reinstall),
            cwd=self.paths.project_root,
            env=self.environment(),
            on_output=on_output,
        )
        self.runner.run(
            self.probe_command(),
            cwd=self.paths.project_root,
            env=self.environment(),
            on_output=on_output,
        )
        save_state(self.paths.state_path, signature)
        self.logger.info("Runtime preparation completed | signature=%s", signature)

    def cancel(self) -> None:
        self.runner.cancel()

    def launch_application(self) -> subprocess.Popen[bytes]:
        command = self.application_command()
        self.logger.info(
            "Launching application | %s",
            subprocess.list2cmdline([str(part) for part in command]),
        )
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(
            command,
            cwd=self.paths.project_root,
            env=self.environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )


def explain_uv_installation() -> str:
    return (
        "uvが見つかりません。先にuvをインストールし、ターミナルを開き直してから"
        "ランチャーを再実行してください。\n\nhttps://docs.astral.sh/uv/getting-started/installation/"
    )


def is_windows_supported() -> bool:
    return sys.platform.startswith("win")

