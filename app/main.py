import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import public, router
from app.config import settings
from app.database import Base, engine
from app.errors import DiskError
from app.store import sweep_partials

STATIC_DIR = Path(__file__).resolve().parent / "static"
logger = logging.getLogger("cloud_disk")


class FreshStaticFiles(StaticFiles):
    """Ask the phone to recheck scripts and styles after a rebuild."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = "no-cache"
        return response


def index_html() -> str:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for asset in ("css/app.css", "js/app.js"):
        stamp = int((STATIC_DIR / asset).stat().st_mtime)
        html = html.replace(f"/static/{asset}", f"/static/{asset}?v={stamp}", 1)
    return html


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "thumbs").mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    try:
        from app.paths import disk_root

        removed = sweep_partials(disk_root())
        if removed:
            logger.info("Discarded %s unfinished upload(s)", removed)
    except (DiskError, OSError):
        logger.warning("Skipped cleanup of unfinished uploads")
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(public)
app.include_router(router)
app.mount("/static", FreshStaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(DiskError)
def disk_error(_request, exc: DiskError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": exc.message})


@app.exception_handler(OSError)
def drive_error(_request, _exc: OSError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "The drive stopped responding. Check the USB cable."},
    )


@app.get("/")
def index() -> HTMLResponse:
    return HTMLResponse(index_html(), headers={"Cache-Control": "no-cache"})
