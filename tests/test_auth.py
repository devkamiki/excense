from excense.auth import MsAuth
from excense.config import Settings


def test_auth_web_flow_url(monkeypatch, tmp_path):
    """auth-web builds the authorize URL offline (no network)."""
    monkeypatch.setenv("EXCENSE_CLIENT_ID", "abc-123")
    monkeypatch.setenv("EXCENSE_DATA_DIR", str(tmp_path))
    flow = MsAuth(Settings()).auth_code_url()
    assert "login.microsoftonline.com/common/oauth2/v2.0/authorize" in flow["auth_uri"]
    assert "code_challenge=" in flow["auth_uri"]  # PKCE present
    assert "Calendars.ReadWrite" in flow["auth_uri"]
