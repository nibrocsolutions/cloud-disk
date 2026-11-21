import os
from pathlib import Path

from sqlalchemy.orm import Session

from app.albums import retarget_paths
from app.errors import DiskError
from app.files import describe
from app.locks import DISK
from app.paths import parent_rel, resolve_dir, resolve_inside, safe_name, unique_child, visible_name, within
from app.store import copy_file_safely


def make_folder(parent: str, name: str) -> dict:
    safe = safe_name(name, replace=False)
    with DISK:
        folder, rel = resolve_dir(parent)
        dest = folder / safe
        if dest.exists() or dest.is_symlink():
            raise DiskError("That name is already in this folder.", 409)
        try:
            dest.mkdir()
        except PermissionError as exc:
            raise DiskError("The drive refused the new folder. Mount it so the container can write.", 403) from exc
        child = f"{rel}/{safe}" if rel else safe
        return describe(dest, child)


def _rename_path(src: Path, safe: str) -> Path:
    dest = src.with_name(safe)
    if src.name == safe:
        return src
    conflict = dest.exists() or dest.is_symlink()
    same = False
    if conflict:
        try:
            same = dest.resolve() == src.resolve() and not dest.is_symlink()
        except OSError:
            same = False
    if conflict and not same:
        raise DiskError("That name is already in this folder.", 409)
    try:
        if same or src.name.casefold() == safe.casefold():
            bridge = unique_child(src.parent, f"{Path(safe).stem} renaming{Path(safe).suffix}")
            os.rename(src, bridge)
            dest = bridge.with_name(safe)
            os.rename(bridge, dest)
            return dest
        os.rename(src, dest)
    except PermissionError as exc:
        raise DiskError("The drive refused the rename. Mount it so the container can write.", 403) from exc
    except OSError as exc:
        raise DiskError("The item could not be renamed.", 503) from exc
    return dest


def rename_entry(db: Session, rel: str, name: str) -> dict:
    safe = safe_name(name, replace=False)
    with DISK:
        src, text = resolve_inside(rel)
        dest = _rename_path(src, safe)
        new_rel = f"{parent_rel(text)}/{dest.name}" if parent_rel(text) else dest.name
        if new_rel != text:
            retarget_paths(db, text, new_rel)
        return describe(dest, new_rel)


def _simplify(rels: list[str]) -> list[str]:
    cleaned = []
    for rel in sorted({rel for rel in rels if rel}, key=len):
        if any(within(kept, rel) for kept in cleaned):
            continue
        cleaned.append(rel)
    return cleaned


def move_entries(db: Session, rels: list[str], destination: str) -> dict:
    chosen = _simplify(rels)
    if not chosen:
        raise DiskError("Choose something to move.")
    if len(chosen) > 500:
        raise DiskError("Move fewer items at a time.")
    moved = []
    with DISK:
        dest_path, dest_rel = resolve_dir(destination)
        for raw in chosen:
            src, text = resolve_inside(raw)
            if within(text, dest_rel):
                raise DiskError("A folder can't be moved inside itself.")
            if parent_rel(text) == dest_rel:
                moved.append({"from": text, "to": text, "unchanged": True})
                continue
            target = unique_child(dest_path, src.name)
            try:
                os.rename(src, target)
            except PermissionError as exc:
                raise DiskError("The drive refused the move. Mount it so the container can write.", 403) from exc
            except OSError as exc:
                raise DiskError("The item could not be moved.", 503) from exc
            new_rel = f"{dest_rel}/{target.name}" if dest_rel else target.name
            retarget_paths(db, text, new_rel)
            moved.append({"from": text, "to": new_rel, "unchanged": False})
    return {"moved": moved}


def copy_entries(rels: list[str], destination: str) -> dict:
    chosen = _simplify(rels)
    if not chosen:
        raise DiskError("Choose something to copy.")
    if len(chosen) > 500:
        raise DiskError("Copy fewer items at a time.")
    copied = []
    with DISK:
        _dest_path, dest_rel = resolve_dir(destination)
        for raw in chosen:
            src, text = resolve_inside(raw)
            if src.is_dir() and within(text, dest_rel):
                raise DiskError("A folder can't be copied inside itself.")
            target_rel = _copy_tree(src, dest_rel, src.name)
            copied.append({"from": text, "to": target_rel})
    return {"copied": copied}


def _copy_tree(src: Path, dest_rel: str, name: str) -> str:
    dest_path, dest_rel = resolve_dir(dest_rel)
    target = unique_child(dest_path, name)
    new_rel = f"{dest_rel}/{target.name}" if dest_rel else target.name
    try:
        if src.is_symlink():
            raise DiskError("Links on the drive are left alone.", 403)
        if src.is_dir():
            target.mkdir()
            for entry in os.scandir(src):
                if entry.is_symlink() or not visible_name(entry.name):
                    continue
                child = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    _copy_tree(child, new_rel, entry.name)
                elif entry.is_file(follow_symlinks=False):
                    copy_file_safely(child, target / entry.name)
        elif src.is_file():
            copy_file_safely(src, target)
        else:
            raise DiskError("That item can't be copied.")
    except DiskError:
        raise
    except PermissionError as exc:
        raise DiskError("The drive refused the copy. Mount it so the container can write.", 403) from exc
    except OSError as exc:
        raise DiskError(
            "The copy stopped. Files already copied are complete. Nothing was removed.",
            503,
        ) from exc
    return new_rel
