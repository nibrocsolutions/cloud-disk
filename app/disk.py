import os
import shutil
from pathlib import Path

from app.config import settings
from app.errors import DiskError
from app.files import describe, kind_for
from app.paths import disk_root, normalize_rel, parent_rel, resolve_dir, visible_name
from app.uploads import batch_label, folder_is_empty


def disk_status() -> dict:
    root = disk_root()
    usage = shutil.disk_usage(root)
    limited = bool(settings.expect_min_bytes) and usage.total < settings.expect_min_bytes
    try:
        writable = os.access(root, os.W_OK)
    except OSError:
        writable = False
    return {
        "ready": True,
        "label": settings.disk_label,
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "writable": writable,
        "limited": limited,
        "detail": "",
    }


def crumbs_for(rel: str) -> list[dict]:
    items = [{"name": settings.disk_label, "path": ""}]
    if not rel:
        return items
    built: list[str] = []
    for part in rel.split("/"):
        built.append(part)
        items.append({"name": part, "path": "/".join(built)})
    return items


def _collect(folder: Path, rel: str) -> list[dict]:
    entries = []
    try:
        scan = list(os.scandir(folder))
    except OSError as exc:
        raise DiskError("The drive stopped responding. Check the USB cable.", 503) from exc
    for entry in scan:
        try:
            if entry.is_symlink() or not visible_name(entry.name):
                continue
            child_rel = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir(follow_symlinks=False):
                info = describe(Path(entry.path), child_rel)
                if rel == "Uploads":
                    info["empty"] = folder_is_empty(Path(entry.path))
                entries.append(info)
            elif entry.is_file(follow_symlinks=False):
                entries.append(describe(Path(entry.path), child_rel))
        except OSError:
            continue
    return entries


def _sort_entries(entries: list[dict], sort: str) -> list[dict]:
    folders = [entry for entry in entries if entry["kind"] == "folder"]
    files = [entry for entry in entries if entry["kind"] != "folder"]
    folders.sort(key=lambda entry: entry["name"].casefold())
    if sort == "name":
        files.sort(key=lambda entry: entry["name"].casefold())
    elif sort == "size":
        files.sort(key=lambda entry: ((entry["size"] or 0), entry["name"].casefold()), reverse=True)
    else:
        files.sort(key=lambda entry: entry["modified"], reverse=True)
    return folders + files


def list_dir(rel: str | None, *, kind: str, sort: str, offset: int, limit: int) -> dict:
    folder, text = resolve_dir(rel)
    entries = _collect(folder, text)
    if kind in {"image", "video", "audio", "other"}:
        entries = [entry for entry in entries if entry["kind"] == "folder" or entry["kind"] == kind]
    entries = _sort_entries(entries, sort if sort in {"name", "modified", "size"} else "modified")
    total = len(entries)
    offset = max(0, offset)
    limit = min(max(1, limit), 240)
    page = entries[offset : offset + limit]
    recent = recent_uploads() if text == "" else []
    return {
        "path": text,
        "parent": parent_rel(text),
        "crumbs": crumbs_for(text),
        "entries": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "truncated": False,
        "recent": recent,
    }


def recent_uploads(limit: int = 6) -> list[dict]:
    root = disk_root()
    folder = root / "Uploads"
    if not folder.is_dir() or folder.is_symlink():
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
    items = []
    for entry in found[:limit]:
        rel = f"Uploads/{entry.name}"
        info = describe(Path(entry.path), rel)
        info["label"] = batch_label(entry.name)
        items.append(info)
    return items


def search(query: str, *, kind: str, limit: int) -> dict:
    text = " ".join(query.split())
    if len(text) < 2:
        raise DiskError("Type at least two letters to search.")
    needle = text.casefold()
    root = disk_root()
    limit = min(max(1, limit), 80)
    found: list[dict] = []
    scanned = 0
    truncated = False
    max_scan = 50000
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        kept_dirs = []
        for name in dirnames:
            child = current / name
            if name.startswith(".") or child.is_symlink():
                continue
            kept_dirs.append(name)
            scanned += 1
            if needle in name.casefold() and (kind == "all" or kind == "folder"):
                rel = _walk_rel(root, child)
                try:
                    found.append(describe(child, rel))
                except OSError:
                    pass
            if scanned >= max_scan or len(found) >= limit:
                truncated = True
                break
        dirnames[:] = [] if truncated else kept_dirs
        if truncated:
            break
        for name in filenames:
            scanned += 1
            child = current / name
            if not visible_name(name) or child.is_symlink() or not child.is_file():
                continue
            file_kind = kind_for(name)
            if needle in name.casefold() and (kind == "all" or kind == file_kind):
                rel = _walk_rel(root, child)
                try:
                    found.append(describe(child, rel))
                except OSError:
                    continue
            if scanned >= max_scan or len(found) >= limit:
                truncated = scanned >= max_scan or len(found) >= limit
                break
        if truncated or len(found) >= limit or scanned >= max_scan:
            if len(found) >= limit or scanned >= max_scan:
                truncated = True
            break
    return {"query": text, "entries": found[:limit], "truncated": truncated, "scanned": scanned}


def _walk_rel(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()
