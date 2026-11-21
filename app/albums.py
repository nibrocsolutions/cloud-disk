from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.errors import DiskError
from app.models import Album, AlbumItem
from app.paths import disk_root, normalize_rel, resolve_inside


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_album_name(name: str) -> str:
    cleaned = " ".join(str(name or "").replace("\x00", "").split())
    if not cleaned or len(cleaned) > 80:
        raise DiskError("Give the album a name up to 80 characters.")
    return cleaned


def album_or_404(db: Session, album_id: int) -> Album:
    album = db.get(Album, album_id)
    if album is None:
        raise DiskError("That album is not here.", 404)
    return album


def list_albums(db: Session) -> list[dict]:
    albums = db.query(Album).order_by(Album.created_at.desc(), Album.id.desc()).all()
    counts = {album.id: 0 for album in albums}
    if counts:
        rows = db.query(AlbumItem).filter(AlbumItem.album_id.in_(counts)).all()
        for row in rows:
            counts[row.album_id] = counts.get(row.album_id, 0) + 1
    return [
        {
            "id": album.id,
            "name": album.name,
            "created_at": album.created_at.isoformat(),
            "count": counts.get(album.id, 0),
            "cover": _cover(db, album.id),
        }
        for album in albums
    ]


def _cover(db: Session, album_id: int) -> str | None:
    row = (
        db.query(AlbumItem)
        .filter(AlbumItem.album_id == album_id)
        .order_by(AlbumItem.added_at.asc(), AlbumItem.id.asc())
        .first()
    )
    return row.path if row else None


def create_album(db: Session, name: str) -> dict:
    album = Album(name=_clean_album_name(name), created_at=_now())
    db.add(album)
    db.commit()
    db.refresh(album)
    return {"id": album.id, "name": album.name, "created_at": album.created_at.isoformat(), "count": 0, "cover": None}


def rename_album(db: Session, album_id: int, name: str) -> dict:
    album = album_or_404(db, album_id)
    album.name = _clean_album_name(name)
    db.commit()
    return {"id": album.id, "name": album.name}


def remove_album(db: Session, album_id: int) -> dict:
    """Remove the album list entry. Files on the drive are not touched."""
    album = album_or_404(db, album_id)
    db.query(AlbumItem).filter(AlbumItem.album_id == album.id).delete()
    db.delete(album)
    db.commit()
    return {"removed": album_id}


def _existing_file(rel: str) -> str:
    text = normalize_rel(rel)
    _path, text = resolve_inside(text, expect="file")
    return text


def add_items(db: Session, album_id: int, paths: list[str]) -> dict:
    album = album_or_404(db, album_id)
    if not paths:
        raise DiskError("Choose a file to add.")
    if len(paths) > 500:
        raise DiskError("Add fewer files at a time.")
    added = 0
    already = 0
    for raw in paths:
        rel = _existing_file(raw)
        exists = db.query(AlbumItem).filter_by(album_id=album.id, path=rel).one_or_none()
        if exists:
            already += 1
            continue
        db.add(AlbumItem(album_id=album.id, path=rel, added_at=_now()))
        added += 1
    db.commit()
    return {"added": added, "already": already, "count": db.query(AlbumItem).filter_by(album_id=album.id).count()}


def detach_items(db: Session, album_id: int, paths: list[str]) -> dict:
    """Take paths out of the album. The files stay on the drive."""
    album = album_or_404(db, album_id)
    removed = 0
    for raw in paths:
        rel = normalize_rel(raw)
        row = db.query(AlbumItem).filter_by(album_id=album.id, path=rel).one_or_none()
        if row is None:
            continue
        db.delete(row)
        removed += 1
    db.commit()
    return {"detached": removed}


def album_detail(db: Session, album_id: int) -> dict:
    album = album_or_404(db, album_id)
    rows = db.query(AlbumItem).filter_by(album_id=album.id).order_by(AlbumItem.added_at.desc(), AlbumItem.id.desc()).all()
    root = disk_root()
    items = []
    for row in rows:
        path = root / row.path
        missing = path.is_symlink() or not path.is_file()
        item = {
            "path": row.path,
            "name": row.path.rsplit("/", 1)[-1],
            "missing": missing,
            "kind": "other",
            "size": None,
            "modified": row.added_at.isoformat(),
        }
        if not missing:
            try:
                from app.files import describe

                item = describe(path, row.path)
                item["missing"] = False
            except OSError:
                item["missing"] = True
        items.append(item)
    return {
        "id": album.id,
        "name": album.name,
        "created_at": album.created_at.isoformat(),
        "count": len(items),
        "items": items,
    }


def retarget_paths(db: Session, old: str, new: str) -> None:
    if old == new:
        return
    rows = db.query(AlbumItem).all()
    prefix = old + "/"
    planned: list[tuple[AlbumItem, str]] = []
    for row in rows:
        if row.path == old:
            planned.append((row, new))
        elif row.path.startswith(prefix):
            planned.append((row, new + row.path[len(old) :]))
    by_album: dict[int, list[tuple[AlbumItem, str]]] = {}
    for row, path in planned:
        by_album.setdefault(row.album_id, []).append((row, path))
    for album_id, changes in by_album.items():
        current = {
            item.path: item.id
            for item in db.query(AlbumItem).filter_by(album_id=album_id).all()
        }
        claimed = set()
        for row, path in changes:
            owner = current.get(path)
            if path in claimed or (owner is not None and owner != row.id):
                db.delete(row)
                continue
            current.pop(row.path, None)
            row.path = path
            current[path] = row.id
            claimed.add(path)
    db.commit()


def export_albums(db: Session) -> dict:
    albums = []
    for album in db.query(Album).order_by(Album.id.asc()).all():
        items = (
            db.query(AlbumItem)
            .filter_by(album_id=album.id)
            .order_by(AlbumItem.id.asc())
            .all()
        )
        albums.append(
            {
                "name": album.name,
                "created_at": album.created_at.isoformat(),
                "items": [{"path": item.path, "added_at": item.added_at.isoformat()} for item in items],
            }
        )
    return {"app": "cloud-disk", "albums": albums}


def import_albums(db: Session, payload: dict) -> dict:
    """Replace the album list. Files on the drive are not changed."""
    albums = payload.get("albums") if isinstance(payload, dict) else None
    if not isinstance(albums, list):
        raise DiskError("That backup is not an album list.")
    db.query(AlbumItem).delete()
    db.query(Album).delete()
    count_albums = 0
    count_items = 0
    for entry in albums:
        if not isinstance(entry, dict):
            continue
        try:
            name = _clean_album_name(str(entry.get("name", "")))
        except DiskError:
            continue
        created = _parse_time(entry.get("created_at")) or _now()
        album = Album(name=name, created_at=created)
        db.add(album)
        db.flush()
        count_albums += 1
        for item in entry.get("items") or []:
            if not isinstance(item, dict):
                continue
            try:
                rel = normalize_rel(str(item.get("path", "")))
            except DiskError:
                continue
            if not rel:
                continue
            db.add(AlbumItem(album_id=album.id, path=rel, added_at=_parse_time(item.get("added_at")) or _now()))
            count_items += 1
    db.commit()
    return {"albums": count_albums, "items": count_items}


def _parse_time(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
