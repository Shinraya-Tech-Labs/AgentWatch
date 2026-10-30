from datetime import datetime
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from panoptis.cli.main import app
from panoptis.security.license import LicenseError

runner = CliRunner()


def test_upgrade_dry_run():
    result = runner.invoke(app, ["upgrade", "--dry-run"])

    assert result.exit_code == 0
    assert "DRY-RUN" in result.stdout
    assert "Would open browser to checkout" in result.stdout
    assert "Dry-run complete" in result.stdout


@patch("panoptis.cli.main._active_entitlement")
def test_upgrade_status_free(mock_entitlement):
    mock_entitlement.return_value = None

    result = runner.invoke(app, ["upgrade", "--status"])

    assert result.exit_code == 0
    assert "Tier:" in result.stdout
    assert "Free" in result.stdout


@patch("panoptis.cli.main._active_entitlement")
def test_upgrade_status_premium(mock_entitlement):
    entitlement = MagicMock()
    entitlement.tier = "Premium"
    entitlement.subject = "sid@example.com"
    entitlement.expires_at = datetime(2027, 1, 1)

    mock_entitlement.return_value = entitlement

    result = runner.invoke(app, ["upgrade", "--status"])

    assert result.exit_code == 0
    assert "AgentWatch Premium" in result.stdout
    assert "Premium" in result.stdout
    assert "sid@example.com" in result.stdout
    assert "2027-01-01" in result.stdout


@patch("panoptis.cli.main._license_public_key")
def test_upgrade_activate_no_public_key(mock_key):
    mock_key.return_value = None

    result = runner.invoke(
        app,
        ["upgrade", "--activate", "dummy-token"],
    )

    assert result.exit_code == 1
    assert "No license public key configured" in result.stdout


@patch("panoptis.cli.main._license_public_key")
@patch("panoptis.security.license.verify_entitlement")
def test_upgrade_activate_invalid_token(mock_verify, mock_key):
    mock_key.return_value = "PUBLIC_KEY"
    mock_verify.side_effect = LicenseError("Invalid token")

    result = runner.invoke(
        app,
        ["upgrade", "--activate", "bad-token"],
    )

    assert result.exit_code == 1
    assert "Entitlement rejected" in result.stdout
    assert "Invalid token" in result.stdout


@patch("panoptis.security.entitlement_store.store_entitlement_token")
@patch("panoptis.security.license.verify_entitlement")
@patch("panoptis.cli.main._license_public_key")
def test_upgrade_activate_success(
    mock_key,
    mock_verify,
    mock_store,
):
    entitlement = MagicMock()
    entitlement.tier = "Premium"

    mock_key.return_value = "PUBLIC_KEY"
    mock_verify.return_value = entitlement
    mock_store.return_value = "license.jwt"

    result = runner.invoke(
        app,
        ["upgrade", "--activate", "good-token"],
    )

    assert result.exit_code == 0
    assert "Premium activated" in result.stdout

    mock_verify.assert_called_once()
    mock_store.assert_called_once_with("good-token")


@patch("panoptis.security.checkout.checkout_url")
@patch("panoptis.security.checkout.new_session")
@patch("webbrowser.open")
def test_upgrade_no_browser(
    mock_browser,
    mock_session,
    mock_checkout_url,
):
    mock_session.return_value = "session"
    mock_checkout_url.return_value = "https://example.com/checkout"

    result = runner.invoke(
        app,
        ["upgrade", "--no-browser"],
    )

    assert result.exit_code == 0
    assert "Checkout URL:" in result.stdout
    assert "https://example.com/checkout" in result.stdout

    mock_browser.assert_not_called()


def test_cli_license_public_key_loading_robustness(monkeypatch, tmp_path):
    """
    Verify that _license_public_key in CLI main.py handles:
    - missing files
    - permission errors
    - invalid encoding
    """
    import sys

    cli_main = sys.modules["panoptis.cli.main"]
    from pathlib import Path

    # 1. Clear env to test files
    monkeypatch.delenv("AGENTWATCH_LICENSE_PUBLIC_KEY", raising=False)

    # 3. File not found
    monkeypatch.setenv("AGENTWATCH_LICENSE_PUBLIC_KEY_FILE", str(tmp_path / "non_existent_key.pem"))
    assert cli_main._license_public_key() is None

    # 4. Permission error
    key_file = tmp_path / "key.pem"
    key_file.touch()
    monkeypatch.setenv("AGENTWATCH_LICENSE_PUBLIC_KEY_FILE", str(key_file))

    def mock_read_text_permission_error(*args, **kwargs):
        raise PermissionError("Access Denied")

    monkeypatch.setattr(Path, "read_text", mock_read_text_permission_error)
    assert cli_main._license_public_key() is None

    # 5. UnicodeDecodeError
    def mock_read_text_decode_error(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(Path, "read_text", mock_read_text_decode_error)
    assert cli_main._license_public_key() is None

    # 6. Generic OSError
    def mock_read_text_os_error(*args, **kwargs):
        raise OSError("Disk failure")

    monkeypatch.setattr(Path, "read_text", mock_read_text_os_error)
    assert cli_main._license_public_key() is None
