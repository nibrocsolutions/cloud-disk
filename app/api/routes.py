import json

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.albums import (
    add_items,
    album_detail,
    create_album,
    detach_items,
    export_albums,
    import_albums,
    list_albums,
    remove_album,
    rename_album,
)
from app.auth import COOKIE, access_token, code_matches, require_access, unlocked
from app.config import settings
from app.database import get_db
from app.disk import disk_status, list_dir, search
from app.errors import DiskError
from app.files import INLINE_TYPES, media_type_for
from app.organize import copy_entries, make_folder, move_entries, rename_entry
from app.paths import resolve_inside
from app.thumbs import ensure_jpeg
from app.uploads import create_batch, list_batches, save_upload

public = APIRouter(prefix="/api")
router = APIRouter(prefix="/api", dependencies=[Depends(require_access)])


class UnlockIn(BaseModel):
    code: str = Field(default="", max_length=200)


class FolderIn(BaseModel):
    parent: str = ""
    name: str = Field(max_length=180)


class RenameIn(BaseModel):
    path: str
    name: str = Field(max_length=180)


class TransferIn(BaseModel):
    paths: list[str] = Field(default_factory=list, max_length=500)
    destination: str = ""


class AlbumIn(BaseModel):
    name: str = Field(max_length=80)


class ItemIn(BaseModel):
    paths: list[str] = Field(default_factory=list, max_length=500)


def _cookie(response: Response) -> None:
    response.set_cookie(
        COOKIE,
        access_token(),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 400,
        path="/",
    )


@public.get("/health")
def health() -> dict:
    ready = True
    try:
        from app.paths import disk_root

        disk_root()
    except DiskError:
        ready = False
    return {"status": "ok", "disk": "ready" if ready else "missing", "app": settings.app_name}


@public.get("/session")
def session(request: Request) -> dict:
    return {"required": bool(settings.access_code), "unlocked": unlocked(request)}


@public.post("/session")
def unlock(body: UnlockIn) -> JSONResponse:
    if settings.access_code and not code_matches(body.code):
        raise HTTPException(status_code=401, detail="That code is not right.")
    response = JSONResponse({"required": bool(settings.access_code), "unlocked": True})
    if settings.access_code:
        _cookie(response)
    return response


@public.post("/session/lock")
def lock() -> JSONResponse:
    response = JSONResponse({"required": bool(settings.access_code), "unlocked": not settings.access_code})
    response.delete_cookie(COOKIE, path="/")
    return response


@router.get("/status")
def status() -> dict:
    try:
        return disk_status()
    except DiskError as exc:
        return {
            "ready": False,
            "label": settings.disk_label,
            "total": 0,
            "used": 0,
            "free": 0,
            "writable": False,
            "limited": False,
            "detail": exc.message,
        }


@router.get("/browse")
def browse(
    path: str = "",
    kind: str = "all",
    sort: str = "modified",
    offset: int = 0,
    limit: int = 90,
) -> dict:
    if kind not in {"all", "image", "video", "audio", "other"}:
        raise DiskError("That filter is not available.")
    return list_dir(path, kind=kind, sort=sort, offset=offset, limit=limit)


@router.get("/search")
def find(q: str = "", kind: str = "all", limit: int = 80) -> dict:
    if kind not in {"all", "image", "video", "audio", "other"}:
        raise DiskError("That filter is not available.")
    return search(q, kind=kind, limit=limit)


@router.get("/file")
def read_file(path: str, download: int = 0) -> FileResponse:
    file_path, _rel = resolve_inside(path, expect="file")
    media = media_type_for(file_path.name)
    inline = download == 0 and media in INLINE_TYPES
    return FileResponse(
        file_path,
        media_type=media,
        filename=file_path.name,
        content_disposition_type="inline" if inline else "attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"},
    )


@router.get("/media")
def media(path: str, size: str = "thumb") -> FileResponse:
    file_path, rel = resolve_inside(path, expect="file")
    jpeg = ensure_jpeg(file_path, rel, "preview" if size == "preview" else "thumb")
    return FileResponse(
        jpeg,
        media_type="image/jpeg",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"},
    )


@router.get("/uploads/batches")
def batches() -> dict:
    return {"batches": list_batches()}


@router.post("/uploads/batches")
def open_batch() -> dict:
    return create_batch()


@router.post("/uploads/batches/{batch_id}/files")
def upload_file(batch_id: str, file: UploadFile = File(...)) -> dict:
    try:
        return save_upload(batch_id, file.filename or "photo", file.file)
    finally:
        file.file.close()


@router.post("/folders")
def folder(body: FolderIn) -> dict:
    return make_folder(body.parent, body.name)


@router.post("/rename")
def rename(body: RenameIn, db: Session = Depends(get_db)) -> dict:
    return rename_entry(db, body.path, body.name)


@router.post("/move")
def move(body: TransferIn, db: Session = Depends(get_db)) -> dict:
    return move_entries(db, body.paths, body.destination)


@router.post("/copy")
def copy(body: TransferIn) -> dict:
    return copy_entries(body.paths, body.destination)


@router.get("/albums")
def albums(db: Session = Depends(get_db)) -> dict:
    return {"albums": list_albums(db)}


@router.post("/albums")
def new_album(body: AlbumIn, db: Session = Depends(get_db)) -> dict:
    return create_album(db, body.name)


@router.get("/albums/{album_id}")
def one_album(album_id: int, db: Session = Depends(get_db)) -> dict:
    return album_detail(db, album_id)


@router.post("/albums/{album_id}/rename")
def album_rename(album_id: int, body: AlbumIn, db: Session = Depends(get_db)) -> dict:
    return rename_album(db, album_id, body.name)


@router.post("/albums/{album_id}/remove")
def album_remove(album_id: int, db: Session = Depends(get_db)) -> dict:
    return remove_album(db, album_id)


@router.post("/albums/{album_id}/items")
def album_add(album_id: int, body: ItemIn, db: Session = Depends(get_db)) -> dict:
    return add_items(db, album_id, body.paths)


@router.post("/albums/{album_id}/items/detach")
def album_detach(album_id: int, body: ItemIn, db: Session = Depends(get_db)) -> dict:
    return detach_items(db, album_id, body.paths)


@router.get("/backup/albums")
def backup_albums(db: Session = Depends(get_db)) -> JSONResponse:
    return JSONResponse(
        export_albums(db),
        headers={"Content-Disposition": "attachment; filename=cloud-disk-albums.json"},
    )


@router.post("/backup/albums")
def restore_albums(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    raw = file.file.read(2_000_001)
    if len(raw) > 2_000_000:
        raise HTTPException(status_code=413, detail="That album backup is too large.")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DiskError("That backup is not an album list.") from exc
    return import_albums(db, payload)
