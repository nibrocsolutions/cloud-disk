import io
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image


def _jpeg(color=(20, 90, 140)) -> bytes:
    image = Image.new("RGB", (32, 24), color)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG")
    return buffer.getvalue()


def _batch(client) -> dict:
    response = client.post("/api/uploads/batches")
    assert response.status_code == 200
    return response.json()


def _upload(client, batch_id, name, payload):
    return client.post(
        f"/api/uploads/batches/{batch_id}/files",
        files={"file": (name, payload, "image/jpeg")},
    )


def test_health_and_home(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    page = client.get("/")
    assert page.status_code == 200
    assert "Cloud Disk" in page.text
    assert "Upload" in page.text
    assert "Organize" in page.text


def test_upload_uses_a_dated_folder_and_never_overwrites(client, disk):
    batch = _batch(client)
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    assert batch["id"].startswith(today)
    assert batch["folder"] == f"Uploads/{batch['id']}"
    first = _upload(client, batch["id"], "IMG_0001.JPG", b"original-bytes")
    assert first.status_code == 200
    saved = disk / "Uploads" / batch["id"] / "IMG_0001.JPG"
    assert saved.read_bytes() == b"original-bytes"
    second = _upload(client, batch["id"], "IMG_0001.JPG", b"different")
    assert second.status_code == 200
    assert saved.read_bytes() == b"original-bytes"
    assert (disk / "Uploads" / batch["id"] / "IMG_0001 (2).JPG").read_bytes() == b"different"
    listed = client.get("/api/browse", params={"path": batch["folder"]})
    names = {entry["name"] for entry in listed.json()["entries"]}
    assert names == {"IMG_0001.JPG", "IMG_0001 (2).JPG"}


def test_upload_name_cannot_escape_the_folder(client, disk):
    batch = _batch(client)
    response = _upload(client, batch["id"], "../outside.jpg", b"inside")
    assert response.status_code == 200
    assert (disk / "Uploads" / batch["id"] / "outside.jpg").read_bytes() == b"inside"
    assert not (disk / "outside.jpg").exists()


def test_two_batches_get_different_folders(client, disk):
    first = _batch(client)
    second = _batch(client)
    assert first["id"] != second["id"]
    assert (disk / "Uploads" / first["id"]).is_dir()
    assert (disk / "Uploads" / second["id"]).is_dir()


def test_browse_hides_links_partials_and_dotfiles(client, disk):
    (disk / "keep.jpg").write_bytes(_jpeg())
    (disk / ".hidden.jpg").write_bytes(b"nope")
    (disk / "half.jpg.cloud-disk-part").write_bytes(b"partial")
    outside = disk.parent / "secret.txt"
    outside.write_text("secret")
    (disk / "leak.txt").symlink_to(outside)
    listed = client.get("/api/browse")
    names = {entry["name"] for entry in listed.json()["entries"]}
    assert names == {"keep.jpg"}
    blocked = client.get("/api/file", params={"path": "leak.txt"})
    assert blocked.status_code == 403
    escaped = client.get("/api/file", params={"path": "../secret.txt"})
    assert escaped.status_code == 400
    assert outside.read_text() == "secret"


def test_search_finds_a_nested_name(client, disk):
    folder = disk / "Trips" / "Beach"
    folder.mkdir(parents=True)
    (folder / "sunset.jpg").write_bytes(_jpeg())
    (disk / ".skip" / "sunset.jpg").parent.mkdir()
    (disk / ".skip" / "sunset.jpg").write_bytes(b"hidden")
    found = client.get("/api/search", params={"q": "sunset"})
    assert found.status_code == 200
    paths = [entry["path"] for entry in found.json()["entries"]]
    assert paths == ["Trips/Beach/sunset.jpg"]


def test_preview_respects_phone_orientation(client, disk):
    image = Image.new("RGB", (40, 16), "red")
    exif = image.getexif()
    exif[274] = 6
    path = disk / "sideways.jpg"
    image.save(path, "JPEG", exif=exif)
    response = client.get("/api/media", params={"path": "sideways.jpg", "size": "thumb"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    with Image.open(io.BytesIO(response.content)) as thumb:
        assert thumb.size[1] > thumb.size[0]


def test_missing_drive_is_a_clear_error(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "disk_root", settings.disk_root / "not-mounted")
    status = client.get("/api/status")
    assert status.status_code == 200
    assert status.json()["ready"] is False
    browse = client.get("/api/browse")
    assert browse.status_code == 503


def test_organize_move_copy_and_rename_keep_bytes(client, disk):
    (disk / "album.jpg").write_bytes(b"photo-bytes")
    (disk / "notes.txt").write_bytes(b"notes")
    made = client.post("/api/folders", json={"parent": "", "name": "Keep"})
    assert made.status_code == 200
    moved = client.post("/api/move", json={"paths": ["album.jpg"], "destination": "Keep"})
    assert moved.status_code == 200
    assert moved.json()["moved"][0]["to"] == "Keep/album.jpg"
    assert (disk / "Keep" / "album.jpg").read_bytes() == b"photo-bytes"
    assert not (disk / "album.jpg").exists()
    copied = client.post("/api/copy", json={"paths": ["notes.txt"], "destination": "Keep"})
    assert copied.status_code == 200
    assert (disk / "notes.txt").read_bytes() == b"notes"
    assert (disk / "Keep" / "notes.txt").read_bytes() == b"notes"
    renamed = client.post("/api/rename", json={"path": "Keep/notes.txt", "name": "letter.txt"})
    assert renamed.status_code == 200
    assert (disk / "Keep" / "letter.txt").read_bytes() == b"notes"
    clash = client.post("/api/folders", json={"parent": "", "name": "Keep"})
    assert clash.status_code == 409
    nested = client.post("/api/move", json={"paths": ["Keep"], "destination": "Keep"})
    assert nested.status_code == 400
    slash = client.post("/api/folders", json={"parent": "", "name": "../Nope"})
    assert slash.status_code == 400


def test_move_does_not_overwrite(client, disk):
    (disk / "Keep").mkdir()
    (disk / "Keep" / "album.jpg").write_bytes(b"already")
    (disk / "album.jpg").write_bytes(b"incoming")
    moved = client.post("/api/move", json={"paths": ["album.jpg"], "destination": "Keep"})
    assert moved.status_code == 200
    assert (disk / "Keep" / "album.jpg").read_bytes() == b"already"
    assert (disk / "Keep" / "album (2).jpg").read_bytes() == b"incoming"


def test_albums_do_not_remove_files(client, disk):
    (disk / "day.jpg").write_bytes(b"day")
    (disk / "Keep").mkdir()
    album = client.post("/api/albums", json={"name": "Summer"})
    assert album.status_code == 200
    album_id = album.json()["id"]
    added = client.post(f"/api/albums/{album_id}/items", json={"paths": ["day.jpg"]})
    assert added.status_code == 200
    moved = client.post("/api/move", json={"paths": ["day.jpg"], "destination": "Keep"})
    assert moved.status_code == 200
    detail = client.get(f"/api/albums/{album_id}")
    assert detail.json()["items"][0]["path"] == "Keep/day.jpg"
    detached = client.post(f"/api/albums/{album_id}/items/detach", json={"paths": ["Keep/day.jpg"]})
    assert detached.status_code == 200
    assert (disk / "Keep" / "day.jpg").read_bytes() == b"day"
    removed = client.post(f"/api/albums/{album_id}/remove")
    assert removed.status_code == 200
    assert (disk / "Keep" / "day.jpg").read_bytes() == b"day"
    backup = client.get("/api/backup/albums")
    assert backup.status_code == 200
    restored = client.post(
        "/api/backup/albums",
        files={"file": ("albums.json", json.dumps({"albums": [{"name": "Restored", "items": [{"path": "Keep/day.jpg"}]}]}).encode(), "application/json")},
    )
    assert restored.status_code == 200
    again = client.get("/api/albums")
    assert again.json()["albums"][0]["name"] == "Restored"
    assert (disk / "Keep" / "day.jpg").read_bytes() == b"day"


def test_access_code_gates_the_drive(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "access_code", "harbor")
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/browse").status_code == 401
    wrong = client.post("/api/session", json={"code": "nope"})
    assert wrong.status_code == 401
    right = client.post("/api/session", json={"code": "harbor"})
    assert right.status_code == 200
    assert client.get("/api/status").status_code == 200
    client.post("/api/session/lock")
    assert client.get("/api/browse").status_code == 401
