import os
from pathlib import Path

from werkzeug.datastructures import FileStorage

from app.security import random_upload_name, validate_upload_file


class InvalidUploadError(ValueError):
    pass


def save_private_upload(
    upload: FileStorage,
    destination: str | os.PathLike[str],
    allowed_extensions: set[str],
    max_bytes: int,
) -> tuple[str, str]:
    validated_name = validate_upload_file(upload, allowed_extensions, max_bytes)
    if not validated_name:
        raise InvalidUploadError("The uploaded file is invalid or unsupported.")

    filename = random_upload_name(validated_name)
    folder = Path(destination)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    upload.save(path)
    return filename, str(path)
