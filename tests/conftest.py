import os
import shutil
import tempfile
from pathlib import Path

_root = Path(tempfile.mkdtemp(prefix="cloud-disk-"))
_data = _root / "data"
_disk = _root / "disk"
_data.mkdir()
_disk.mkdir()
os.environ["DATA_DIR"] = str(_data)
os.environ["DISK_ROOT"] = str(_disk)
os.environ["DATABASE_URL"] = f"sqlite:///{_data / 'test.db'}"
os.environ["EXPECT_MIN_BYTES"] = "0"
os.environ["ACCESS_CODE"] = ""
os.environ["APP_TZ"] = "America/New_York"
os.environ["DISK_LABEL"] = "Drive"

import pytest


@pytest.fixture(autouse=True)
def fresh():
    import app.models  # noqa: F401
    from app.config import settings
    from app.database import Base, engine

    disk = settings.disk_root
    if disk.exists():
        shutil.rmtree(disk)
    disk.mkdir()
    thumbs = settings.data_dir / "thumbs"
    if thumbs.exists():
        shutil.rmtree(thumbs)
    engine.dispose()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def disk():
    from app.config import settings

    return settings.disk_root


@pytest.fixture
def client(fresh):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
