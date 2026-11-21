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


def _file_size(path: Path) -> int | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return path.stat().st_size
    except OSError:
        return None


def _duplicate(rel_folder: str, name: str, size: int) -> dict:
    return {"duplicate": True, "name": name, "size": size, "path": f"{rel_folder}/{name}"}


def find_duplicates(batch_id: str, files: list[tuple[str, int]]) -> list[int]:
    """Indexes of files already in this folder, or repeated in this list.

    A duplicate is the same safe name and the same size. A different size is
    a different file and is not listed here.
    """
    with DISK:
        folder, _rel = _batch_folder(batch_id)
        on_disk: dict[str, int] = {}
        try:
            for entry in os.scandir(folder):
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                if not visible_name(entry.name):
                    continue
                try:
                    on_disk[entry.name] = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
        except OSError as exc:
            raise DiskError("The folder could not be read. Check that the drive is connected.", 503) from exc
        skip: list[int] = []
        claimed: set[tuple[str, int]] = set()
        for index, (name, size) in enumerate(files):
            try:
                safe = safe_name(name or "photo", replace=True)
                declared = int(size)
            except (DiskError, TypeError, ValueError):
                continue
            if declared < 0:
                continue
            key = (safe, declared)
            if on_disk.get(safe) == declared or key in claimed:
                skip.append(index)
            else:
                claimed.add(key)
        return skip


def save_upload(batch_id: str, filename: str, source, size: int | None = None) -> dict:
    safe = safe_name(filename or "photo", replace=True)
    declared = size if isinstance(size, int) and size >= 0 else None
    with DISK:
        folder, rel_folder = _batch_folder(batch_id)
        current = folder / safe
        existing = _file_size(current)
        if existing is not None and declared is not None and declared == existing:
            logger.info("Skipped duplicate %s/%s (%s bytes)", rel_folder, safe, existing)
            return _duplicate(rel_folder, safe, existing)
        final = unique_child(folder, safe)
        try:
            written = stream_to_final(
                final,
                source,
                abandon_if_size=existing if existing is not None else None,
            )
        except DiskError:
            raise
        except PermissionError as exc:
            raise DiskError("The drive refused the photo. Mount it so the container can write.", 403) from exc
        except OSError as exc:
            raise DiskError("The photo could not be written. Check that the drive is connected.", 503) from exc
        if written < 0:
            logger.info("Skipped duplicate %s/%s (%s bytes)", rel_folder, safe, existing)
            return _duplicate(rel_folder, safe, existing or 0)
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
