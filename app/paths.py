import os
import re
from pathlib import Path

from app.config import settings
from app.errors import DiskError

PARTIAL_SUFFIX = ".cloud-disk-part"

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"}
_RESERVED.update(f"COM{i}" for i in range(1, 10))
_RESERVED.update(f"LPT{i}" for i in range(1, 10))


def disk_root() -> Path:
    root = settings.disk_root
    try:
        root = root.resolve()
    except OSError as exc:
        raise DiskError("The portable drive is not mounted.", 503) from exc
    if not root.is_dir():
        raise DiskError("The portable drive is not mounted.", 503)
    return root


def normalize_rel(raw: str | None) -> str:
    if raw is None:
        raw = ""
    text = str(raw).replace("\\", "/").strip()
    if text.startswith("/"):
        raise DiskError("That path is not on the drive.")
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == ".." or "\x00" in part:
            raise DiskError("That path is not on the drive.")
        parts.append(part)
    return "/".join(parts)


def safe_name(name: str, *, replace: bool = False) -> str:
    raw = str(name or "").replace("\x00", "")
    if not replace and ("/" in raw or "\\" in raw):
        raise DiskError("Use a name without slashes.")
    cleaned = raw.replace("\\", "/").split("/")[-1].strip()
    if replace:
        cleaned = _ILLEGAL.sub("_", cleaned)
    elif _ILLEGAL.search(cleaned):
        raise DiskError('Use a name without : * ? " < > | or slashes.')
    cleaned = cleaned.rstrip(" .")
    if cleaned in ("", ".", "..") or cleaned.startswith("."):
        raise DiskError("That name is not allowed.")
    if cleaned.endswith(PARTIAL_SUFFIX):
        raise DiskError("That name is not allowed.")
    stem = Path(cleaned).stem.upper()
    if stem in _RESERVED:
        raise DiskError("That name is reserved on the drive.")
    if len(cleaned) > 180:
        suffix = Path(cleaned).suffix[:20]
        cleaned = cleaned[: 180 - len(suffix)].rstrip(" .") + suffix
    if not cleaned or cleaned.startswith("."):
        raise DiskError("That name is not allowed.")
    return cleaned


def _taken(path: Path) -> bool:
    if path.is_symlink() or path.exists():
        return True
    partial = Path(str(path) + PARTIAL_SUFFIX)
    return partial.is_symlink() or partial.exists()


def unique_child(folder: Path, name: str) -> Path:
    candidate = folder / name
    stem = Path(name).stem
    suffix = Path(name).suffix
    number = 2
    while _taken(candidate):
        candidate = folder / f"{stem} ({number}){suffix}"
        number += 1
        if number > 5000:
            raise DiskError("Too many items already use that name.")
    return candidate


def resolve_inside(rel: str | None, *, expect: str | None = None) -> tuple[Path, str]:
    root = disk_root()
    text = normalize_rel(rel)
    current = root
    if text:
        for part in text.split("/"):
            current = current / part
            try:
                if current.is_symlink():
                    raise DiskError("Links on the drive stay hidden.", 403)
                if not current.exists():
                    raise DiskError("That item is not on the drive.", 404)
            except OSError as exc:
                raise DiskError("The drive stopped responding. Check the USB cable.", 503) from exc
    try:
        if text and not (current.is_dir() or current.is_file()):
            raise DiskError("That item is not a file or folder.")
        if expect == "dir" and not current.is_dir():
            raise DiskError("That is not a folder.")
        if expect == "file" and not current.is_file():
            raise DiskError("That is not a file.")
    except OSError as exc:
        raise DiskError("The drive stopped responding. Check the USB cable.", 503) from exc
    return current, text


def resolve_dir(rel: str | None) -> tuple[Path, str]:
    if not normalize_rel(rel):
        return disk_root(), ""
    return resolve_inside(rel, expect="dir")


def relative_of(path: Path) -> str:
    root = disk_root()
    text = path.resolve().relative_to(root).as_posix()
    return "" if text == "." else text


def parent_rel(rel: str) -> str:
    if "/" not in rel:
        return ""
    return rel.rsplit("/", 1)[0]


def within(parent: str, child: str) -> bool:
    return child == parent or child.startswith(parent + "/")


def visible_name(name: str) -> bool:
    if not name or name.startswith(".") or name.endswith(PARTIAL_SUFFIX):
        return False
    return True


def fsync_file(path: Path) -> None:
    with path.open("rb+") as handle:
        os.fsync(handle.fileno())
