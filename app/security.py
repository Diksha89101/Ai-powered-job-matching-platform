import re
from werkzeug.utils import secure_filename


PASSWORD_MIN_LENGTH = 8


def validate_password(password, minimum_length=PASSWORD_MIN_LENGTH):
    if not isinstance(password, str) or len(password) < minimum_length:
        return False, f"Password must be at least {minimum_length} characters long."
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter."
    if not re.search(r"\d", password):
        return False, "Password must contain at least one number."
    return True, ""


def safe_upload_name(filename, allowed_extensions):
    cleaned = secure_filename(filename or "")
    if not cleaned:
        return None
    extension = "." + cleaned.rsplit(".", 1)[1].lower() if "." in cleaned else ""
    if extension not in allowed_extensions:
        return None
    return cleaned
