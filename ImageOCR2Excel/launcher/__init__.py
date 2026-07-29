"""Windows launcher support for the source-distributed application."""

from ImageOCR2Excel.launcher.core import (
    LauncherCancelled,
    LauncherPaths,
    LauncherService,
    LauncherStatus,
    application_handoff_command,
    launch_application_handoff,
    launch_repair_handoff,
    launcher_paths,
    repair_handoff_command,
)

__all__ = [
    "LauncherCancelled",
    "LauncherPaths",
    "LauncherService",
    "LauncherStatus",
    "application_handoff_command",
    "launch_application_handoff",
    "launch_repair_handoff",
    "launcher_paths",
    "repair_handoff_command",
]

