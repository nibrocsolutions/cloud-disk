import logging
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import settings
from app.errors import DiskError
from app.files import describe
from app.locks import DISK
from app.paths import disk_root, safe_name, unique_child, visible_name
from app.store import stream_to_final

logger = logging.getLogger("cloud_disk")

BATCH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:-\d+)?$")


def batch_label(batch_id: str) -> str:
    match = BATCH_RE.fullmatch(batch_id)
    if not match:
        return batch_id
    stamp = batch_id[:19]
    when = datetime.strptime(stamp, "%Y-%m-%d_%H-%M-%S")
    return when.strftime("%b %-d, %Y · %-I:%M %p")


def _uploads_dir() -> Path:
    root = disk_root()
    folder = root / "Uploads"
    if folder.is_symlink():
        raise DiskError("The Uploads folder is a link, so new photos were not saved.", 403)
    if folder.exists() and not folder.is_dir():
        raise DiskError("Uploads is a file on the drive. Rename it from a computer, then try again.")
    return folder


def create_batch() -> dict:
    with DISK:
        folder = _uploads_dir()
        folder.mkdir(exist_ok=True)
        now = datetime.now(ZoneInfo(settings.app_tz))
        base = now.strftime("%Y-%m-%d_%H-%M-%S")
        batch_id = base
        number = 2
        while (folder / batch_id).exists():
            batch_id = f"{base}-{number}"
            number += 1
        (folder / batch_id).mkdir()
    logger.info("Opened upload folder Uploads/%s", batch_id)
    return {
        "id": batch_id,
        "folder": f"Uploads/{batch_id}",
        "label": batch_label(batch_id),
        "created_at": now.isoformat(),
    }


def list_batches() -> list[dict]:
    try:
        folder = _uploads_dir()
    except DiskError:
        raise
    if not folder.is_dir():
        return []
    names = []
    try:
        for entry in os.scandir(folder):
            if entry.is_dir(follow_symlinks=False) and not entry.is_symlink() and BATCH_RE.fullmatch(entry.name):
                names.append(entry.name)
    except OSError as exc:
        raise DiskError("The drive stopped responding. Check the USB cable.", 503) from exc
    names.sort(reverse=True)
    return [{"id": name, "folder": f"Uploads/{name}", "label": batch_label(name)} for name in names]


def _batch_folder(batch_id: str) -> tuple[Path, str]:
    if not BATCH_RE.fullmatch(batch_id or ""):
        raise DiskError("That upload folder is not valid.", 404)
    folder = _uploads_dir() / batch_id
    if folder.is_symlink() or not folder.is_dir():
        raise DiskError("That upload folder is not on the drive.", 404)
    return folder, f"Uploads/{batch_id}"


def save_upload(batch_id: str, filename: str, source) -> dict:
    safe = safe_name(filename or "photo", replace=True)
    with DISK:
        folder, rel_folder = _batch_folder(batch_id)
        final = unique_child(folder, safe)
        try:
            stream_to_final(final, source)
        except DiskError:
            raise
        except PermissionError as exc:
            raise DiskError("The drive refused the photo. Mount it so the container can write.", 403) from exc
        except OSError as exc:
            raise DiskError("The photo could not be written. Check that the drive is connected.", 503) from exc
        saved = describe(final, f"{rel_folder}/{final.name}")
    logger.info("Saved %s (%s bytes)", saved["path"], saved["size"])
    return saved


def iter_upload_folders() -> list[Path]:
    folder = disk_root() / "Uploads"
    if not folder.is_dir() or folder.is_symlink():
        return []
    found = []
    for entry in os.scandir(folder):
        if entry.name.startswith(".") or entry.is_symlink():
            continue
        if entry.is_dir(follow_symlinks=False) and visible_name(entry.name) and BATCH_RE.fullmatch(entry.name):
            found.append(Path(entry.path))
    found.sort(key=lambda path: path.name, reverse=True)
    return found
