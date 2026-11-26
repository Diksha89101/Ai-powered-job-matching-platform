import os
import math
import re
import secrets
from typing import Any

from dotenv import load_dotenv

load_dotenv(override=False)


def env_value(name: str, default: Any = None) -> Any:
    value = os.getenv(name, default)

    if not isinstance(value, str):
        return value

    value = value.strip()

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1].strip()

    return value


def env_bool(name: str, default: bool = False) -> bool:
    return str(env_value(name, default)).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def required_secret(name: str) -> str:
    value = env_value(name)

    if not isinstance(value, str) or not value:
        raise RuntimeError(
            f"{name} must be configured in the environment."
        )
    frequencies = {character: value.count(character) / len(value) for character in set(value)}
    estimated_entropy = -sum(
        frequency * math.log2(frequency)
        for frequency in frequencies.values()
    ) * len(value)
    if (
        len(value) < 32
        or estimated_entropy < 128
        or re.search(r"(.)\1{15,}", value)
        or any(
            marker in value.lower()
            for marker in ("replace-with", "change-me", "your-", "example", "secret")
        )
    ):
        raise RuntimeError(
            f"{name} must be a non-placeholder secret of at least 32 characters "
            "with sufficient variation."
        )

    return value


def positive_int(name: str, default: int) -> int:
    try:
        value = int(env_value(name, default))
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{name} must be a positive integer.") from error
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer.")
    return value


def build_config() -> dict[str, Any]:
    secret_key = required_secret("SECRET_KEY")
    environment = env_value("FLASK_ENV", "").lower()
    is_production = environment == "production"
    upload_folder = env_value("UPLOAD_FOLDER") or os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "instance",
        "uploads",
    )
    profile_image_folder = env_value("PROFILE_IMAGE_FOLDER") or os.path.join(
        upload_folder,
        "profile-images",
    )
    static_folder = os.path.realpath(
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
    )
    for folder_name, folder_path in (
        ("UPLOAD_FOLDER", upload_folder),
        ("PROFILE_IMAGE_FOLDER", profile_image_folder),
    ):
        resolved_folder = os.path.realpath(folder_path)
        try:
            is_public = os.path.commonpath((static_folder, resolved_folder)) == static_folder
        except ValueError:
            is_public = False
        if is_public:
            raise RuntimeError(f"{folder_name} must be outside the public static directory.")
    mail_password = env_value("MAIL_PASSWORD")
    if isinstance(mail_password, str):
        mail_password = mail_password.replace(" ", "")

    return {
        "SECRET_KEY": secret_key,
        "MONGO_URI": env_value(
            "MONGO_URI",
            "mongodb://localhost:27017/jobplatform",
        ),
        "UPLOAD_FOLDER": upload_folder,
        "PROFILE_IMAGE_FOLDER": profile_image_folder,
        "MAX_CONTENT_LENGTH": 16 * 1024 * 1024,
        "MAX_RESUME_SIZE": 10 * 1024 * 1024,
        "MAX_PROFILE_IMAGE_SIZE": 2 * 1024 * 1024,
        "ACCESS_TOKEN_TTL_MINUTES": positive_int(
            "ACCESS_TOKEN_TTL_MINUTES",
            15,
        ),
        "REFRESH_TOKEN_TTL_DAYS": positive_int(
            "REFRESH_TOKEN_TTL_DAYS",
            7,
        ),
        "REFRESH_COOKIE_SECURE": (
            True
            if is_production
            else env_bool("REFRESH_COOKIE_SECURE", False)
        ),
        "REFRESH_COOKIE_NAME": "jobplatform_refresh",
        "REFRESH_CSRF_COOKIE_NAME": "jobplatform_refresh_csrf",
        "REFRESH_COOKIE_PATH": "/api/auth",
        "SESSION_COOKIE_SECURE": is_production,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "RATELIMIT_STORAGE_URI": env_value(
            "RATELIMIT_STORAGE_URI",
            "memory://",
        ),
        "ALLOW_DEV_RESET_LINK": (
            env_bool("ALLOW_DEV_RESET_LINK", False)
            and environment == "development"
        ),
        "SECURITY_HEADERS_ENABLED": env_bool("SECURITY_HEADERS_ENABLED", True),
        "HSTS_ENABLED": env_bool("HSTS_ENABLED", is_production),
        "CSP_REPORT_ONLY": env_bool("CSP_REPORT_ONLY", True),
        "PREFERRED_URL_SCHEME": "https" if is_production else "http",
        "MIN_MATCH_PERCENTAGE_TO_APPLY": int(
            env_value("MIN_MATCH_PERCENTAGE_TO_APPLY", 50)
        ),
        "MAIL_SERVER": env_value(
            "MAIL_SERVER",
            "smtp.gmail.com",
        ),
        "MAIL_PORT": int(
            env_value("MAIL_PORT", 587)
        ),
        "MAIL_USE_TLS": env_bool(
            "MAIL_USE_TLS",
            True,
        ),
        "MAIL_USE_SSL": env_bool(
            "MAIL_USE_SSL",
            False,
        ),
        "MAIL_USERNAME": env_value("MAIL_USERNAME"),
        "MAIL_PASSWORD": mail_password,
        "MAIL_DEFAULT_SENDER": (
            env_value("MAIL_DEFAULT_SENDER")
            or env_value("MAIL_USERNAME")
        ),
        "CORS_ORIGINS": env_value(
            "CORS_ORIGINS",
            "",
        ),
        "PASSWORD_MIN_LENGTH": positive_int("PASSWORD_MIN_LENGTH", 8),
        "PASSWORD_RESET_TTL_HOURS": positive_int(
            "PASSWORD_RESET_TTL_HOURS",
            1,
        ),
        "FLASK_ENV": environment,
    }


def generate_secret() -> str:
    """Return a URL-safe secret suitable for SECRET_KEY."""
    return secrets.token_urlsafe(48)