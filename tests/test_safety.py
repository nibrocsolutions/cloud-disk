from pathlib import Path

from app.errors import DiskError
from app.store import discard_partial
from app.uploads import save_upload


def test_no_http_delete(client):
    from app.main import app

    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        assert "DELETE" not in methods
    response = client.delete("/api/file", params={"path": "keep.jpg"})
    assert response.status_code == 405


def test_source_never_removes_finished_files():
    offenders = []
    for path in Path("app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in ("rmtree", "os.remove", ".rmdir(", "shutil.move")):
            offenders.append(path.name)
        if ".unlink(" in text and path.name != "store.py":
            offenders.append(f"{path.name}:unlink")
    assert offenders == []
    store = (Path("app") / "store.py").read_text(encoding="utf-8")
    assert store.count(".unlink(") == 1
    assert "PARTIAL_SUFFIX" in store


def test_discard_partial_refuses_real_files(disk):
    photo = disk / "keep.jpg"
    photo.write_bytes(b"safe")
    try:
        discard_partial(photo)
    except DiskError:
        pass
    else:
        raise AssertionError("A finished file was treated as removable")
    assert photo.read_bytes() == b"safe"


def test_partial_link_does_not_remove_its_target(disk):
    photo = disk / "keep.jpg"
    photo.write_bytes(b"safe")
    link = disk / "keep.jpg.cloud-disk-part"
    link.symlink_to(photo)
    try:
        discard_partial(link)
    except DiskError:
        pass
    assert photo.read_bytes() == b"safe"
    assert link.is_symlink()


def test_failed_upload_does_not_leave_a_partial(client, disk):
    batch = client.post("/api/uploads/batches").json()

    class Boom:
        def __init__(self):
            self.sent = False

        def read(self, _size):
            if not self.sent:
                self.sent = True
                return b"half"
            raise OSError("unplugged")

    try:
        save_upload(batch["id"], "pic.jpg", Boom())
    except DiskError:
        pass
    else:
        raise AssertionError("A broken upload was reported as saved")
    folder = disk / "Uploads" / batch["id"]
    assert list(folder.iterdir()) == []
    assert not (disk / "pic.jpg").exists()
