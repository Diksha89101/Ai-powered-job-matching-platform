import hashlib
import re
import secrets
import zipfile
from pathlib import PurePath
from typing import Any, Mapping
from urllib.parse import urlparse

from werkzeug.utils import secure_filename


PASSWORD_MIN_LENGTH = 8


def validate_password(
    password: Any,
    minimum_length: int = PASSWORD_MIN_LENGTH,
) -> tuple[bool, str]:
    if not isinstance(password, str) or len(password) < minimum_length:
        return False, f"Password must be at least {minimum_length} characters long."
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter."
    if not re.search(r"\d", password):
        return False, "Password must contain at least one number."
    return True, ""


def safe_upload_name(
    filename: str | None,
    allowed_extensions: set[str],
) -> str | None:
    cleaned = secure_filename(filename or "")
    if not cleaned:
        return None
    extension = "." + cleaned.rsplit(".", 1)[1].lower() if "." in cleaned else ""
    if extension not in allowed_extensions:
        return None
    return cleaned


def random_upload_name(filename: str) -> str:
    extension = PurePath(filename).suffix.lower()
    return f"{secrets.token_hex(24)}{extension}"


def validate_upload_file(
    upload: Any,
    allowed_extensions: set[str],
    max_bytes: int,
) -> str | None:
    filename = safe_upload_name(upload.filename, allowed_extensions)
    if not filename:
        return None

    stream = upload.stream
    try:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(0)
        if size <= 0 or size > max_bytes:
            return None

        header = stream.read(1024)
        stream.seek(0)
        extension = PurePath(filename).suffix.lower()

        if extension == ".pdf" and b"%PDF-" not in header:
            return None
        if extension in {".jpg", ".jpeg"} and not header.startswith(b"\xff\xd8\xff"):
            return None
        if extension == ".png" and not header.startswith(
            b"\x89PNG\r\n\x1a\n"
        ):
            return None
        if extension == ".gif" and not header.startswith((b"GIF87a", b"GIF89a")):
            return None
        if extension == ".doc" and not header.startswith(
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        ):
            return None
        if extension == ".docx":
            try:
                with zipfile.ZipFile(stream) as document:
                    if "word/document.xml" not in document.namelist():
                        return None
            except (OSError, zipfile.BadZipFile):
                return None
        return filename
    finally:
        stream.seek(0)


def token_digest(token_id: str) -> str:
    return hashlib.sha256(token_id.encode("utf-8")).hexdigest()


def valid_csrf_token(cookie_value: str | None, header_value: str | None) -> bool:
    return bool(
        cookie_value
        and header_value
        and secrets.compare_digest(cookie_value, header_value)
    )


def valid_email(email: Any) -> bool:
    return bool(
        isinstance(email, str)
        and len(email) <= 254
        and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
    )


def valid_http_url(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and not parsed.username
        and not parsed.password
    )


def valid_text_fields(
    data: Mapping[str, Any],
    limits: Mapping[str, int],
) -> bool:
    return all(
        isinstance(data[field], str)
        and len(data[field]) <= max_length
        for field, max_length in limits.items()
        if field in data
    )
