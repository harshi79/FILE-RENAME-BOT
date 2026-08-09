"""
Error-handling helpers for the Telegram handler layer.

Two narrowly-scoped production problems are solved here.

1. ``handler_exception_surface`` used to be emitted by a ``RawUpdateHandler``
   that Pyrogram calls for *every* update (Pyrogram has no error-handler
   concept). It logged with ``exc_info=True`` while no exception was being
   handled, so ``logging`` fell back to ``sys.exc_info()`` -> ``(None, None,
   None)`` and rendered the useless ``"NoneType: None"``.
   :func:`surface_exceptions` instead wraps a handler callback: when the
   callback raises, the ORIGINAL exception (type, message, traceback) is
   logged and then re-raised unchanged. Nothing is swallowed, replaced or
   fabricated.

2. Telegram replies ``400 MESSAGE_NOT_MODIFIED`` when an edit would leave the
   message exactly as it is (idempotent menus such as ``adm:stats`` or
   ``adm:jobs:0``). :func:`is_message_not_modified` recognises exactly that
   condition so callers can treat it as a harmless no-op; every other API
   error is left untouched.

Privacy: only metadata (handler name, update type, user id, chat id) plus the
exception type/message/traceback are logged. Message contents and callback
payloads are never logged here, and credential-looking substrings (URL
passwords, bot tokens) are masked via :mod:`utils.sanitize`.
"""
from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, Dict, Optional, Tuple

from utils.logging import get_logger
from utils.sanitize import mask_secrets

log = get_logger("handlers.errors")

# Pyrogram's flow-control signals are ordinary exceptions but are NOT errors:
# they must pass through the surfacing wrapper untouched and unlogged.
try:  # pragma: no cover - trivial import guard
    from pyrogram import ContinuePropagation, StopPropagation

    _FLOW_CONTROL: Tuple[type, ...] = (StopPropagation, ContinuePropagation)
except Exception:  # pragma: no cover - Pyrogram always present in production
    _FLOW_CONTROL = ()

try:  # pragma: no cover - trivial import guard
    from pyrogram.errors import MessageNotModified as _MessageNotModified
except Exception:  # pragma: no cover
    _MessageNotModified = None  # type: ignore[assignment]

#: Telegram's error identifier for "the edit changes nothing".
MESSAGE_NOT_MODIFIED_ID = "MESSAGE_NOT_MODIFIED"

#: Event name kept identical to the one production log searches already use.
HANDLER_EXCEPTION_EVENT = "handler_exception_surface"


# ──────────────────────────────────────────────────────────────────────────
# Exception surfacing (Issue 1)
# ──────────────────────────────────────────────────────────────────────────
def exception_details(exc: BaseException) -> Dict[str, str]:
    """
    Return the ORIGINAL exception's type and message, credential-masked.

    The traceback is not returned here: it is attached to the log record via
    ``exc_info`` so standard logging tooling renders it verbatim.
    """
    return {
        "exc_type": type(exc).__name__,
        "exc_message": mask_secrets(str(exc)) or "<no message>",
    }


def _first_int_id(*candidates: Any) -> Optional[int]:
    """Return the first candidate exposing an integer ``id`` (else ``None``).

    Only plain integers are accepted so a log payload always stays JSON
    serialisable, whatever object the update carries.
    """
    for candidate in candidates:
        value = getattr(candidate, "id", None)
        if isinstance(value, int):
            return value
    return None


def update_metadata(update: Any) -> Dict[str, Optional[Any]]:
    """
    Extract *metadata only* from a Message / CallbackQuery / raw update.

    Never returns message text, captions, file names or callback payloads.
    """
    meta: Dict[str, Optional[Any]] = {"update_type": type(update).__name__}
    try:
        message = getattr(update, "message", None)
        meta["user_id"] = _first_int_id(
            getattr(update, "from_user", None),
            getattr(message, "from_user", None),
        )
        # A Message carries ``chat`` directly; a CallbackQuery carries it on the
        # message it belongs to.
        meta["chat_id"] = _first_int_id(
            getattr(update, "chat", None),
            getattr(message, "chat", None),
        )
    except Exception:  # pragma: no cover - metadata must never break logging
        pass
    return meta


def log_handler_exception(exc: BaseException, *, logger=None,
                          event: str = HANDLER_EXCEPTION_EVENT, **context: Any) -> None:
    """
    Log ``exc`` with its real type, message and traceback.

    ``exc_info=exc`` passes the exception object itself, so ``logging`` never
    has to guess via ``sys.exc_info()`` — which is what produced the bogus
    ``"NoneType: None"`` output in production.
    """
    extra: Dict[str, Any] = {k: v for k, v in context.items() if v is not None}
    extra.update(exception_details(exc))
    (logger or log).error(event, extra=extra, exc_info=exc)


def surface_exceptions(callback: Callable, *, handler_name: Optional[str] = None,
                       logger=None) -> Callable:
    """
    Wrap an update-handler callback so failures are logged, then re-raised.

    Works for every handler kind (Message, CallbackQuery, raw): the update
    object is always the first argument after the client.
    """
    if getattr(callback, "__exception_surface__", False):
        return callback

    name = handler_name or getattr(callback, "__name__", repr(callback))

    def _report(exc: BaseException, args: Tuple[Any, ...]) -> None:
        meta = update_metadata(args[0]) if args else {}
        log_handler_exception(exc, logger=logger, handler=name, **meta)

    if inspect.iscoroutinefunction(callback):
        @functools.wraps(callback)
        async def async_wrapper(client, *args, **kwargs):
            try:
                return await callback(client, *args, **kwargs)
            except _FLOW_CONTROL:
                raise
            except Exception as exc:  # noqa: BLE001 - logged and re-raised as-is
                _report(exc, args)
                raise

        async_wrapper.__exception_surface__ = True  # type: ignore[attr-defined]
        return async_wrapper

    @functools.wraps(callback)
    def sync_wrapper(client, *args, **kwargs):
        try:
            return callback(client, *args, **kwargs)
        except _FLOW_CONTROL:
            raise
        except Exception as exc:  # noqa: BLE001 - logged and re-raised as-is
            _report(exc, args)
            raise

    sync_wrapper.__exception_surface__ = True  # type: ignore[attr-defined]
    return sync_wrapper


def install_exception_surface(app) -> int:
    """
    Wrap the callbacks of all already-registered handlers.

    Registration itself is untouched: groups, ordering and handler objects stay
    exactly as they were — only each ``handler.callback`` is decorated. Returns
    the number of callbacks wrapped.
    """
    wrapped = 0
    groups = getattr(getattr(app, "dispatcher", None), "groups", None) or {}
    for handlers in groups.values():
        for handler in handlers:
            callback = getattr(handler, "callback", None)
            if callback is None or getattr(callback, "__exception_surface__", False):
                continue
            handler.callback = surface_exceptions(callback)
            wrapped += 1
    return wrapped


# ──────────────────────────────────────────────────────────────────────────
# MESSAGE_NOT_MODIFIED (Issue 2)
# ──────────────────────────────────────────────────────────────────────────
def is_message_not_modified(exc: BaseException) -> bool:
    """
    True only for Telegram's ``400 MESSAGE_NOT_MODIFIED`` condition.

    Recognised via the Pyrogram exception class, the RPC error id, or the error
    id embedded in the message text (older/generic ``RPCError`` instances).
    Any other exception returns False so it keeps propagating normally.
    """
    if _MessageNotModified is not None and isinstance(exc, _MessageNotModified):
        return True
    if getattr(exc, "ID", None) == MESSAGE_NOT_MODIFIED_ID:
        return True
    return MESSAGE_NOT_MODIFIED_ID in str(exc)
