"""
Retry helper with exponential backoff.

Only transient errors (network / database / timeout) are retried. Permanent
errors (invalid file, unsupported type, bad filename, cancellation) propagate
immediately.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Tuple, Type

import pyrogram.errors

# Errors that should never be retried.
PERMANENT_ERRORS: Tuple[Type[BaseException], ...] = (
    ValueError,
    FileNotFoundError,
    IsADirectoryError,
    PermissionError,
)

# Telegram errors that indicate a permanent problem for this operation.
PERMANENT_RPC = (
    pyrogram.errors.VolumeLocNotFound,
)


class CancellationError(Exception):
    """Raised when a job has been cancelled."""


def is_permanent(exc: BaseException) -> bool:
    if isinstance(exc, PERMANENT_ERRORS):
        return True
    if isinstance(exc, CancellationError):
        return True
    return False


async def retry_async(
    operation: Callable[..., Awaitable],
    *args,
    max_retries: int = 3,
    base_delay: float = 1.0,
    is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    **kwargs,
):
    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        if is_cancelled is not None and await is_cancelled():
            raise CancellationError("job cancelled")
        try:
            return await operation(*args, **kwargs)
        except CancellationError:
            raise
        except Exception as exc:  # noqa: BLE001 - we re-raise appropriately
            last_exc = exc
            if is_permanent(exc):
                raise
            # FloodWait has its own required wait.
            if isinstance(exc, pyrogram.errors.FloodWait):
                delay = float(getattr(exc, "value", 5))
            else:
                delay = min(base_delay * (2 ** attempt), 20)
            if attempt >= max_retries:
                break
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc
