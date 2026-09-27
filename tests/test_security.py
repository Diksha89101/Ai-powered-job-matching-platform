from app.security import safe_upload_name, validate_password


def test_password_requires_reasonable_strength():
    assert validate_password("Weakpass1")[0] is True
    assert validate_password("short1")[0] is False
    assert validate_password("lowercase1")[0] is False
    assert validate_password("UPPERCASE1")[0] is False


def test_upload_name_is_sanitized_and_extension_checked():
    assert safe_upload_name("../../resume.pdf", {".pdf"}) == "resume.pdf"
    assert safe_upload_name("resume.exe", {".pdf"}) is None
    assert safe_upload_name("", {".pdf"}) is None
