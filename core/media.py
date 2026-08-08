"""
Media information extraction from Pyrogram messages.

Kept separate from the Telegram handlers so the validation / rename core can
be unit-tested without a live Telegram connection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from core.validation import classify_telegram_media


@dataclass(frozen=True)
class MediaInfo:
    filename: str
    size: int
    mime_type: str
    media_type: str          # document | photo | ...
    file_id: str
    file_ref: Optional[str]  # may be None; stored for completeness
    chat_id: int
    message_id: int


def extract_media_info(message) -> Optional[MediaInfo]:
    """
    Pull file metadata out of a Pyrogram message. Returns None when there is
    no downloadable file. For documents we use the document's attributes; for
    other types we derive a best-effort filename.
    """
    media_type = classify_telegram_media(message)

    document = getattr(message, "document", None)
    if document is not None:
        filename = ""
        for attr in getattr(document, "attributes", []) or []:
            # FileAttribute has .file_name; avoid importing the raw type for
            # easier unit testing.
            fname = getattr(attr, "file_name", None)
            if fname:
                filename = fname
                break
        return MediaInfo(
            filename=filename or f"file_{document.file_id}",
            size=int(getattr(document, "file_size", 0) or 0),
            mime_type=getattr(document, "mime_type", "") or "",
            media_type="document",
            file_id=document.file_id,
            file_ref=getattr(document, "file_ref", None),
            chat_id=message.chat.id,
            message_id=message.id,
        )

    if media_type == "photo":
        photo = message.photo
        size = 0
        file_id = ""
        if photo is not None:
            sizes = getattr(photo, "sizes", None) or []
            if sizes:
                biggest = sizes[-1]
                size = int(getattr(biggest, "file_size", 0) or 0)
                file_id = getattr(biggest, "file_id", "") or getattr(photo, "file_id", "")
            else:
                file_id = getattr(photo, "file_id", "")
        return MediaInfo(
            filename="photo.jpg",
            size=size,
            mime_type="image/jpeg",
            media_type="photo",
            file_id=file_id,
            file_ref=None,
            chat_id=message.chat.id,
            message_id=message.id,
        )

    if media_type == "video":
        v = message.video
        return MediaInfo(
            filename=getattr(v, "file_name", "") or "video.mp4",
            size=int(getattr(v, "file_size", 0) or 0),
            mime_type=getattr(v, "mime_type", "video/mp4"),
            media_type="video",
            file_id=getattr(v, "file_id", ""),
            file_ref=getattr(v, "file_ref", None),
            chat_id=message.chat.id,
            message_id=message.id,
        )

    if media_type == "audio":
        a = message.audio
        return MediaInfo(
            filename=getattr(a, "file_name", "") or "audio.mp3",
            size=int(getattr(a, "file_size", 0) or 0),
            mime_type=getattr(a, "mime_type", "audio/mpeg"),
            media_type="audio",
            file_id=getattr(a, "file_id", ""),
            file_ref=getattr(a, "file_ref", None),
            chat_id=message.chat.id,
            message_id=message.id,
        )

    # No downloadable file we understand.
    return None
