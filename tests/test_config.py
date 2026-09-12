from pathlib import Path

from excense.config import Settings


def test_env_overrides(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("EXCENSE_CLIENT_ID", "abc-123")
    monkeypatch.setenv("EXCENSE_TENANT", "contoso.onmicrosoft.com")
    monkeypatch.setenv("EXCENSE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EXCENSE_PORT", "9999")

    s = Settings()

    assert s.client_id == "abc-123"
    assert s.tenant == "contoso.onmicrosoft.com"
    assert s.authority == "https://login.microsoftonline.com/contoso.onmicrosoft.com"
    assert s.port == 9999
    assert s.state_db_path.parent == tmp_path / "state"
    assert any(scope.endswith("Calendars.ReadWrite") for scope in s.graph_scopes)
    assert all(scope.startswith("https://") for scope in s.graph_scopes)


def test_defaults():
    s = Settings(_env_file=None)
    assert s.username == "excense"
    assert s.sync_interval_seconds == 300
    assert s.passwd_path == s.data_dir / "auth" / "htpasswd"