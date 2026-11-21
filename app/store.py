import logging
import os
import shutil
from pathlib import Path

from app.errors import DiskError
from app.paths import PARTIAL_SUFFIX, disk_root, fsync_file

logger = logging.getLogger("cloud_disk")


def _lexical_inside(path: Path, root: Path) -> bool:
    lexical = os.path.normpath(str(path if path.is_absolute() else root / path))
    root_s = os.path.normpath(str(root))
    return lexical == root_s or lexical.startswith(root_s + os.sep)


def discard_partial(path: Path) -> None:
    """Drop an unfinished upload this app did not finish writing.

    Finished files are never removed. The name has to end in the partial suffix,
    and links are never followed.
    """
    if path.is_symlink() or not path.name.endswith(PARTIAL_SUFFIX):
        raise DiskError("Refusing to remove a finished file.", 500)
    root = disk_root()
    if not _lexical_inside(path, root):
        raise DiskError("Refusing to remove a file outside the drive.", 500)
    if path.is_file():
        path.unlink()


def sweep_partials(root: Path) -> int:
    removed = 0
    if not root.exists():
        return 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if not name.startswith(".") and not (Path(dirpath) / name).is_symlink()]
        for name in filenames:
            if not name.endswith(PARTIAL_SUFFIX):
                continue
            try:
                discard_partial(Path(dirpath) / name)
                removed += 1
            except (DiskError, OSError):
                logger.warning("Left an unfinished upload in place: %s", name)
    return removed


def stream_to_final(final: Path, source, abandon_if_size: int | None = None) -> int:
    part = Path(str(final) + PARTIAL_SUFFIX)
    try:
        with part.open("xb") as out:
            total = 0
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                total += len(chunk)
            out.flush()
            os.fsync(out.fileno())
        if abandon_if_size is not None and total == abandon_if_size:
            _discard_quietly(part)
            return -1
        os.replace(part, final)
        return total
    except Exception:
        _discard_quietly(part)
        raise


def copy_file_safely(src: Path, dest: Path) -> None:
    part = Path(str(dest) + PARTIAL_SUFFIX)
    try:
        shutil.copyfile(src, part)
        try:
            shutil.copystat(src, part, follow_symlinks=False)
        except OSError:
            pass
        fsync_file(part)
        os.replace(part, dest)
    except Exception:
        _discard_quietly(part)
        raise


def _discard_quietly(part: Path) -> None:
    if not part.exists() and not part.is_symlink():
        return
    try:
        discard_partial(part)
    except Exception:
        logger.exception("Could not discard an unfinished file")
