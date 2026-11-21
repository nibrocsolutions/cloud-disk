import hashlib
import hmac

from fastapi import HTTPException, Request

from app.config import settings

COOKIE = "cloud_disk_access"


def code_matches(offered: str) -> bool:
    if not settings.access_code:
        return True
    got = hashlib.sha256(offered.encode("utf-8")).digest()
    expected = hashlib.sha256(settings.access_code.encode("utf-8")).digest()
    return hmac.compare_digest(got, expected)


def access_token() -> str:
    return hashlib.sha256(f"cloud-disk:{settings.access_code}".encode("utf-8")).hexdigest()


def unlocked(request: Request) -> bool:
    if not settings.access_code:
        return True
    cookie = request.cookies.get(COOKIE, "")
    if not cookie:
        return False
    return hmac.compare_digest(cookie, access_token())


def require_access(request: Request) -> None:
    if unlocked(request):
        return
    raise HTTPException(status_code=401, detail="Enter the access code.")
