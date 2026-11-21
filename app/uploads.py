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
from app.paths import disk_root, normalize_rel, safe_name, unique_child, visible_name
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
    info = _folder_info(folder / batch_id, batch_id)
    info["created_at"] = now.isoformat()
    return info


def folder_is_empty(folder: Path) -> bool:
    try:
        with os.scandir(folder) as scan:
            return next(scan, None) is None
    except OSError:
        return False


def _folder_info(path: Path, name: str) -> dict:
    rel = f"Uploads/{name}"
    return {
        "id": name,
        "name": name,
        "path": rel,
        "folder": rel,
        "label": batch_label(name),
        "empty": folder_is_empty(path),
    }


def _child_name(raw: str) -> str:
    text = normalize_rel(raw)
    if text == "Uploads":
        raise DiskError("Choose a folder inside Uploads.")
    if text.startswith("Uploads/"):
        text = text[len("Uploads/") :]
    if not text or "/" in text:
        raise DiskError("Only a folder directly under Uploads can be used.")
    return safe_name(text, replace=False)


def list_batches() -> list[dict]:
    try:
        folder = _uploads_dir()
    except DiskError:
        raise
    if not folder.is_dir():
        return []
    found = []
    try:
        for entry in os.scandir(folder):
            if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
            if not visible_name(entry.name):
                continue
            found.append(entry)
    except OSError as exc:
        raise DiskError("The drive stopped responding. Check the USB cable.", 503) from exc

    def newest(entry):
        try:
            return entry.stat(follow_symlinks=False).st_mtime
        except OSError:
            return 0

    found.sort(key=newest, reverse=True)
    return [_folder_info(Path(entry.path), entry.name) for entry in found]


def create_named_folder(name: str) -> dict:
    safe = _child_name(name)
    with DISK:
        parent = _uploads_dir()
        parent.mkdir(exist_ok=True)
        dest = parent / safe
        if dest.is_symlink():
            raise DiskError("That name is a link under Uploads.", 403)
        if dest.exists():
            raise DiskError("That folder is already under Uploads.", 409)
        try:
            dest.mkdir()
        except PermissionError as exc:
            raise DiskError("The drive refused the new folder. Mount it so the container can write.", 403) from exc
        except OSError as exc:
            raise DiskError("The folder could not be created. Check that the drive is connected.", 503) from exc
    logger.info("Created upload folder Uploads/%s", safe)
    return _folder_info(dest, safe)


def remove_empty_upload_folder(raw: str) -> dict:
    """Remove one empty directory that is a direct child of Uploads.

    rmdir fails unless the directory has no entries at all. Files, folders
    anywhere else, and anything inside the folder are left where they are.
    """
    safe = _child_name(raw)
    with DISK:
        parent = _uploads_dir()
        folder = parent / safe
        if folder.is_symlink() or not folder.is_dir():
            raise DiskError("That folder is not under Uploads.", 404)
        if folder.parent != parent:
            raise DiskError("Only a folder directly under Uploads can be removed.")
        if not folder_is_empty(folder):
            raise DiskError("That folder still has something in it, so it stays.", 409)
        try:
            folder.rmdir()
        except OSError as exc:
            raise DiskError("That folder still has something in it, so it stays.", 409) from exc
    logger.info("Removed empty upload folder Uploads/%s", safe)
    return {"removed": f"Uploads/{safe}"}


def _batch_folder(batch_id: str) -> tuple[Path, str]:
    safe = _child_name(batch_id)
    folder = _uploads_dir() / safe
    if folder.is_symlink() or not folder.is_dir():
        raise DiskError("That upload folder is not on the drive.", 404)
    return folder, f"Uploads/{safe}"


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
        if entry.is_dir(follow_symlinks=False) and visible_name(entry.name):
            found.append(Path(entry.path))
    found.sort(key=lambda path: path.name, reverse=True)
    return found
