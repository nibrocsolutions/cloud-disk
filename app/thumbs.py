import hashlib
import logging
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageOps

from app.config import settings
from app.errors import DiskError
from app.files import kind_for
from app.locks import THUMBS
logger = logging.getLogger("cloud_disk")

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    logger.warning("HEIC previews are unavailable")


def cache_file(rel: str, size: str, mtime_ns: int, length: int) -> Path:
    key = hashlib.sha256(f"{rel}|{mtime_ns}|{length}|{size}".encode()).hexdigest()
    folder = settings.data_dir / "thumbs" / key[:2]
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{key}.jpg"


def ensure_jpeg(src: Path, rel: str, size: str) -> Path:
    if kind_for(src.name) not in {"image", "video"}:
        raise DiskError("There is no preview for that file.", 404)
    st = src.stat()
    dest = cache_file(rel, size, st.st_mtime_ns, st.st_size)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    pixels = 1600 if size == "preview" else 420
    with THUMBS:
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        tmp = dest.with_name(dest.name + ".tmp")
        try:
            if kind_for(src.name) == "video":
                _video_frame(src, tmp, pixels)
            else:
                _image_thumb(src, tmp, pixels)
            if not tmp.is_file() or tmp.stat().st_size == 0:
                raise DiskError("A preview could not be made.", 404)
            tmp.replace(dest)
        except DiskError:
            raise
        except Exception as exc:
            logger.info("Preview failed for %s", rel)
            raise DiskError("A preview could not be made.", 404) from exc
    return dest


def _image_thumb(src: Path, dest: Path, pixels: int) -> None:
    with Image.open(src) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode in ("RGBA", "LA"):
            background = Image.new("RGB", image.size, (16, 18, 22))
            background.paste(image, mask=image.split()[-1])
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")
        image.thumbnail((pixels, pixels))
        image.save(dest, "JPEG", quality=78)


def _video_frame(src: Path, dest: Path, pixels: int) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise DiskError("A preview could not be made.", 404)
    scale = f"scale={pixels}:{pixels}:force_original_aspect_ratio=decrease"
    for stamp in ("1", "0"):
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            stamp,
            "-i",
            str(src),
            "-frames:v",
            "1",
            "-vf",
            scale,
            str(dest),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=45, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            return
    raise DiskError("A preview could not be made.", 404)
