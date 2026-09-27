import os
from dotenv import load_dotenv

load_dotenv(override=True)


def env_value(name, default=None):
    value = os.getenv(name, default)
    if not isinstance(value, str):
        return value
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {""", "'"}:
        value = value[1:-1].strip()
    return value


def env_bool(name, default=False):
    return str(env_value(name, default)).lower() in {"1", "true", "yes", "on"}


def required_secret(name):
    value = env_value(name)
    if not value:
        raise RuntimeError(f"{name} must be configured in the environment.")
    return value


def build_config():
    secret_key = required_secret("SECRET_KEY")
    upload_folder = env_value("UPLOAD_FOLDER", "uploads")
    return {
        "SECRET_KEY": secret_key,
        "MONGO_URI": env_value("MONGO_URI", "mongodb://localhost:27017/jobplatform"),
        "UPLOAD_FOLDER": upload_folder,
        "MAX_CONTENT_LENGTH": 16 * 1024 * 1024,
        "MIN_MATCH_PERCENTAGE_TO_APPLY": int(env_value("MIN_MATCH_PERCENTAGE_TO_APPLY", 50)),
        "MAIL_SERVER": env_value("MAIL_SERVER", "smtp.gmail.com"),
        "MAIL_PORT": int(env_value("MAIL_PORT", 587)),
        "MAIL_USE_TLS": env_bool("MAIL_USE_TLS", True),
        "MAIL_USE_SSL": env_bool("MAIL_USE_SSL", False),
        "MAIL_USERNAME": env_value("MAIL_USERNAME"),
        "MAIL_PASSWORD": env_value("MAIL_PASSWORD"),
        "MAIL_DEFAULT_SENDER": env_value("MAIL_DEFAULT_SENDER") or env_value("MAIL_USERNAME"),
        "CORS_ORIGINS": env_value("CORS_ORIGINS", ""),
        "PASSWORD_MIN_LENGTH": int(env_value("PASSWORD_MIN_LENGTH", 8)),
    }
