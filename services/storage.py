"""
Filesystem storage management.

Each job gets a unique directory under the configured temp root. Files are
streamed to disk (never loaded fully into RAM). Disk space is checked before
download and the directory is always removed in a finally block.
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Tuple

from config import Config
from utils.logging import get_logger

log = get_logger(__name__)

# Safety margin kept free beyond the largest accepted file.
_DISK_SAFETY_MARGIN = 25 * 1024 * 1024


class StorageError(Exception):
    pass


class JobStorage:
    def __init__(self, config: Config) -> None:
        self._config = config
        self.root = config.temp_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        # Validate the id to prevent path traversal.
        try:
            uuid.UUID(job_id)
        except (ValueError, AttributeError) as exc:
            raise StorageError(f"invalid job id: {job_id!r}") from exc
        path = (self.root / job_id).resolve()
        if self.root not in path.parents and path != self.root:
            raise StorageError("job path escapes temp root")
        return path

    def create_job_dir(self, job_id: str) -> Path:
        path = self.job_dir(job_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ensure_disk_space(self, required_bytes: int) -> bool:
        """Return True when the temp filesystem has enough free space."""
        try:
            usage = shutil.disk_usage(str(self.root))
        except OSError as exc:
            log.error("disk_usage_error", extra={"error": str(exc)})
            return False
        needed = required_bytes + _DISK_SAFETY_MARGIN
        return usage.free >= needed

    def verify_file(self, path: Path, expected_size: int) -> Tuple[bool, int]:
        if not path.is_file():
            return False, 0
        actual = path.stat().st_size
        return actual > 0 and actual <= max(expected_size, self._config.max_file_size) * 1.1, actual

    def perform_filesystem_rename(self, src: Path, new_name: str) -> Path:
        """
        Rename on the same filesystem. Returns the destination path.
        The destination is placed in the same job directory.
        """
        if not src.is_file():
            raise StorageError("source file missing")
        dest = src.parent / new_name
        if dest.exists():
            # Extremely unlikely with UUID job dirs, but be safe.
            dest.unlink()
        src.rename(dest)
        return dest

    def cleanup_job(self, job_id: str) -> None:
        try:
            path = self.job_dir(job_id)
        except StorageError:
            return
        if path.exists() and path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            log.info("job_dir_cleaned", extra={"job": job_id})

    def cleanup_all_stale(self) -> int:
        """Remove every job directory under the temp root (used on startup)."""
        removed = 0
        if not self.root.exists():
            return 0
        for child in self.root.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        if removed:
            log.info("stale_dirs_cleaned", extra={"count": removed})
        return removed
