import pytest
from web_agent.security import UnsafeURL, check_readonly_click, excel_safe, safe_identifier, validate_public_url


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "http://localhost:8000", "http://127.0.0.1",
    "http://192.168.1.2", "http://10.0.0.2", "http://169.254.169.254/latest/meta-data",
    "http://internal.local", "http://a.internal", "https://username:secret@example.com/",
])
def test_reject_local_and_private_urls(url):
    with pytest.raises(UnsafeURL):
        validate_public_url(url)


def test_host_allowlist():
    assert validate_public_url("https://docs.python.org/3/", ("python.org",))
    with pytest.raises(UnsafeURL):
        validate_public_url("https://notpython.org", ("python.org",))


def test_excel_injection():
    assert excel_safe("=1+1").startswith("'")
    assert excel_safe(" -cmd").startswith("'")
    assert excel_safe("normal") == "normal"


def test_disallow_dangerous_click():
    with pytest.raises(PermissionError):
        check_readonly_click("Delete all")
    check_readonly_click("Search documents")


def test_session_ids():
    assert safe_identifier("abc-001") == "abc-001"
    assert safe_identifier("../../secret") != "../../secret"
