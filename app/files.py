import mimetypes
from datetime import datetime, timezone
from pathlib import Path

IMAGE_EXT = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".heic",
    ".heif",
    ".bmp",
    ".tif",
    ".tiff",
    ".avif",
    ".dng",
}
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mkv", ".webm", ".avi", ".3gp", ".hevc"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".aiff", ".aif", ".ogg"}

INLINE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/heic",
    "image/heif",
    "image/avif",
    "image/tiff",
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "audio/mpeg",
    "audio/mp4",
    "audio/aac",
    "audio/wav",
    "audio/flac",
    "audio/ogg",
}

mimetypes.add_type("image/heic", ".heic")
mimetypes.add_type("image/heif", ".heif")
mimetypes.add_type("image/avif", ".avif")
mimetypes.add_type("image/x-adobe-dng", ".dng")
mimetypes.add_type("video/quicktime", ".mov")
mimetypes.add_type("video/mp4", ".m4v")
mimetypes.add_type("video/x-matroska", ".mkv")
mimetypes.add_type("audio/mp4", ".m4a")


def kind_for(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return "other"


def media_type_for(name: str) -> str:
    guessed, _encoding = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def describe(path: Path, rel: str) -> dict:
    st = path.stat()
    folder = path.is_dir()
    modified = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    return {
        "name": path.name,
        "path": rel,
        "kind": "folder" if folder else kind_for(path.name),
        "size": None if folder else st.st_size,
        "modified": modified,
    }
