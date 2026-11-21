import io
import json
from datetime import datetime
from urllib.parse import quote
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


def test_same_name_and_size_is_not_uploaded_again(client, disk):
    batch = _batch(client)
    payload = b"same-photo-bytes"
    first = _upload(client, batch["id"], "IMG_0001.JPG", payload)
    assert first.status_code == 200
    assert first.json().get("duplicate") is not True
    folder = disk / "Uploads" / batch["id"]

    again = client.post(
        f"/api/uploads/batches/{batch['id']}/files",
        files={"file": ("IMG_0001.JPG", payload, "image/jpeg")},
        data={"size": str(len(payload))},
    )
    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    assert again.json()["name"] == "IMG_0001.JPG"
    assert {path.name for path in folder.iterdir()} == {"IMG_0001.JPG"}
    assert (folder / "IMG_0001.JPG").read_bytes() == payload

    silent = _upload(client, batch["id"], "IMG_0001.JPG", payload)
    assert silent.status_code == 200
    assert silent.json()["duplicate"] is True
    assert {path.name for path in folder.iterdir()} == {"IMG_0001.JPG"}

    twin = b"x" * len(payload)
    same_size = client.post(
        f"/api/uploads/batches/{batch['id']}/files",
        files={"file": ("IMG_0001.JPG", twin, "image/jpeg")},
        data={"size": str(len(twin))},
    )
    assert same_size.status_code == 200
    assert same_size.json()["duplicate"] is True
    assert (folder / "IMG_0001.JPG").read_bytes() == payload
    assert not (folder / "IMG_0001 (2).JPG").exists()

    checked = client.post(
        f"/api/uploads/batches/{batch['id']}/duplicates",
        json={
            "files": [
                {"name": "IMG_0001.JPG", "size": len(payload)},
                {"name": "IMG_0001.JPG", "size": len(payload)},
                {"name": "other.jpg", "size": 4},
                {"name": "IMG_0001.JPG", "size": len(payload) + 1},
            ]
        },
    )
    assert checked.status_code == 200
    assert checked.json()["skip"] == [0, 1]

    fresh = _batch(client)
    within = client.post(
        f"/api/uploads/batches/{fresh['id']}/duplicates",
        json={
            "files": [
                {"name": "A.JPG", "size": 10},
                {"name": "A.JPG", "size": 10},
                {"name": "A.JPG", "size": 11},
            ]
        },
    )
    assert within.status_code == 200
    assert within.json()["skip"] == [1]


def test_upload_name_cannot_escape_the_folder(client, disk):
    batch = _batch(client)
    response = _upload(client, batch["id"], "../outside.jpg", b"inside")
    assert response.status_code == 200
    assert (disk / "Uploads" / batch["id"] / "outside.jpg").read_bytes() == b"inside"
    assert not (disk / "outside.jpg").exists()


def test_named_upload_folder_can_be_chosen_and_only_empty_ones_go_away(client, disk):
    made = client.post("/api/uploads/folders", json={"name": "Beach Day"})
    assert made.status_code == 200
    folder = made.json()
    assert folder["folder"] == "Uploads/Beach Day"
    assert folder["empty"] is True
    saved = _upload(client, quote(folder["id"]), "pic.jpg", b"pic")
    assert saved.status_code == 200
    assert (disk / "Uploads" / "Beach Day" / "pic.jpg").read_bytes() == b"pic"
    blocked = client.post("/api/uploads/folders/remove", json={"path": "Uploads/Beach Day"})
    assert blocked.status_code == 409
    assert (disk / "Uploads" / "Beach Day" / "pic.jpg").read_bytes() == b"pic"

    empty = client.post("/api/uploads/folders", json={"name": "Later"})
    assert empty.status_code == 200
    listed = {item["name"]: item for item in client.get("/api/uploads/batches").json()["batches"]}
    assert listed["Beach Day"]["empty"] is False
    assert listed["Later"]["empty"] is True
    browsed = {entry["name"]: entry for entry in client.get("/api/browse", params={"path": "Uploads"}).json()["entries"]}
    assert browsed["Later"]["empty"] is True
    assert browsed["Beach Day"]["empty"] is False

    gone = client.post("/api/uploads/folders/remove", json={"path": "Uploads/Later"})
    assert gone.status_code == 200
    assert gone.json()["removed"] == "Uploads/Later"
    assert not (disk / "Uploads" / "Later").exists()
    assert (disk / "Uploads" / "Beach Day" / "pic.jpg").read_bytes() == b"pic"

    (disk / "Keep").mkdir()
    (disk / "Keep" / "stay.jpg").write_bytes(b"stay")
    outside = client.post("/api/uploads/folders/remove", json={"path": "Keep"})
    assert outside.status_code == 404
    assert (disk / "Keep" / "stay.jpg").read_bytes() == b"stay"
    root = client.post("/api/uploads/folders/remove", json={"path": "Uploads"})
    assert root.status_code == 400
    assert (disk / "Uploads").is_dir()

    nested_dir = disk / "Uploads" / "Beach Day" / "inner"
    nested_dir.mkdir()
    nested = client.post("/api/uploads/folders/remove", json={"path": "Uploads/Beach Day/inner"})
    assert nested.status_code == 400
    assert nested_dir.is_dir()

    hidden = disk / "Uploads" / "Quiet"
    hidden.mkdir()
    (hidden / ".keep").write_text("x")
    refused = client.post("/api/uploads/folders/remove", json={"path": "Uploads/Quiet"})
    assert refused.status_code == 409
    assert (hidden / ".keep").read_text() == "x"

    link = disk / "Uploads" / "linked"
    link.symlink_to(disk / "Keep", target_is_directory=True)
    linked = client.post("/api/uploads/folders/remove", json={"path": "Uploads/linked"})
    assert linked.status_code == 404
    assert link.is_symlink()
    assert (disk / "Keep" / "stay.jpg").read_bytes() == b"stay"

    (disk / "Uploads" / "notes.txt").write_text("keep")
    as_file = client.post("/api/uploads/folders/remove", json={"path": "Uploads/notes.txt"})
    assert as_file.status_code == 404
    assert (disk / "Uploads" / "notes.txt").read_text() == "keep"
    slash = client.post("/api/uploads/folders", json={"name": "../Nope"})
    assert slash.status_code == 400
    assert not (disk / "Nope").exists()
    again = client.post("/api/uploads/folders", json={"name": "Beach Day"})
    assert again.status_code == 409


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
