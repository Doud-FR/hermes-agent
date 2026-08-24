"""Regression tests for profile-scoped dashboard Channels endpoints.

Before the ``profile`` parameter existed, ``/api/messaging/platforms`` always
read/wrote the dashboard process's own (root) ``.env`` via ``load_env()`` /
``save_env_value()`` — so a dashboard switched to a freshly created profile
still displayed and persisted the ROOT install's messaging credentials.
These tests pin the new behavior: reads and writes land in the REQUESTED
profile's HERMES_HOME, and the dashboard's own profile stays untouched.
"""
import base64
from io import BytesIO

from PIL import Image, features
import pytest
import yaml
import gateway.status as _gw_status


_VALID_WORKER_BOT_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_1234"
_VALID_BODY_BOT_TOKEN = "987654321:ZYXWVUTSRQPONMLKJIHGFEDCBA_4321"


@pytest.fixture
def isolated_profiles(tmp_path, monkeypatch, _isolate_hermes_home):
    """Isolated default home + one named profile, each with its own .env."""
    from hermes_constants import get_hermes_home
    from hermes_cli import profiles

    default_home = get_hermes_home()
    profiles_root = default_home / "profiles"
    worker_home = profiles_root / "worker_alpha"
    for home in (default_home, worker_home):
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text("{}\n", encoding="utf-8")

    (default_home / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=root-token\n", encoding="utf-8"
    )
    (worker_home / ".env").write_text("", encoding="utf-8")

    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: default_home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: profiles_root)
    return {"default": default_home, "worker_alpha": worker_home}


@pytest.fixture
def client(monkeypatch, isolated_profiles):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    import hermes_state
    from hermes_constants import get_hermes_home
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", get_hermes_home() / "state.db")
    # The dashboard process's os.environ may carry root-install credentials;
    # make sure the scoped path never falls back to them.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


def _telegram(payload):
    return next(p for p in payload["platforms"] if p["id"] == "telegram")


def _email(payload):
    return next(p for p in payload["platforms"] if p["id"] == "email")


def _env_field(platform, key):
    return next(f for f in platform["env_vars"] if f["key"] == key)


def _logo_bytes(
    image_format: str,
    *,
    size: tuple[int, int] = (12, 8),
) -> bytes:
    mode = "RGB" if image_format == "JPEG" else "RGBA"
    output = BytesIO()
    Image.new(mode, size, color="blue").save(output, format=image_format)
    return output.getvalue()


def _put_logo(client, data: bytes, *, profile: str = "worker_alpha"):
    return client.put(
        "/api/messaging/email/signature-logo",
        params={"profile": profile},
        files={"file": ("misleading-name.exe", data, "application/octet-stream")},
    )


def _preview(client, config, *, body="Hello **there**.", profile="worker_alpha"):
    return client.post(
        "/api/messaging/email/preview",
        params={"profile": profile},
        json={"body_markdown": body, "config": config},
    )


class TestProfileScopedMessagingReads:
    def test_email_content_config_defaults_are_typed(self, client, isolated_profiles):
        resp = client.get(
            "/api/messaging/platforms", params={"profile": "worker_alpha"}
        )

        assert resp.status_code == 200
        assert _email(resp.json())["config"] == {
            "rich_html_enabled": False,
            "signature": {
                "enabled": False,
                "text": "",
                "html": "",
                "logo_width": 230,
            },
        }

    def test_scoped_read_does_not_show_root_credentials(
        self, client, isolated_profiles
    ):
        resp = client.get(
            "/api/messaging/platforms", params={"profile": "worker_alpha"}
        )
        assert resp.status_code == 200
        telegram = _telegram(resp.json())
        token = _env_field(telegram, "TELEGRAM_BOT_TOKEN")
        # The worker profile has an empty .env — the root token must not leak.
        assert token["is_set"] is False
        assert telegram["configured"] is False


    def test_unknown_profile_returns_404(self, client, isolated_profiles):
        resp = client.get(
            "/api/messaging/platforms", params={"profile": "no_such_profile"}
        )
        assert resp.status_code == 404

    def test_scoped_read_returns_profile_path_command_and_startup_failure(
        self, client, isolated_profiles, monkeypatch
    ):
        worker_home = isolated_profiles["worker_alpha"]
        (worker_home / ".env").write_text(
            "TELEGRAM_BOT_TOKEN=worker-token\n", encoding="utf-8"
        )
        (worker_home / "config.yaml").write_text(
            yaml.safe_dump({"platforms": {"telegram": {"enabled": True}}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(_gw_status, "get_running_pid", lambda *a, **k: None)
        monkeypatch.setattr(
            _gw_status, "get_running_pid_cached", lambda *a, **k: None
        )
        monkeypatch.setattr(
            _gw_status,
            "read_runtime_status",
            # Accepts path= : the profile-scoped read now passes the
            # profile's own gateway_state.json explicitly rather than
            # relying on process-level HERMES_HOME resolution (#71211).
            lambda *a, **k: {
                "gateway_state": "startup_failed",
                "exit_reason": "all configured messaging platforms failed to connect",
                "platforms": {},
            },
        )

        resp = client.get(
            "/api/messaging/platforms", params={"profile": "worker_alpha"}
        )

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["env_path"] == str(worker_home / ".env")
        assert payload["gateway_start_command"] == (
            "hermes -p worker_alpha gateway start"
        )
        telegram = _telegram(payload)
        assert telegram["state"] == "startup_failed"
        assert telegram["error_code"] == "startup_failed"
        assert telegram["error_message"] == (
            "all configured messaging platforms failed to connect"
        )


class TestProfileScopedMessagingWrites:
    def test_email_content_config_round_trips_without_clobbering_siblings(
        self, client, isolated_profiles
    ):
        worker_home = isolated_profiles["worker_alpha"]
        (worker_home / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "platforms": {
                        "email": {"enabled": True, "poll_interval": 42},
                        "telegram": {"enabled": False},
                    }
                }
            ),
            encoding="utf-8",
        )

        email_config = {
            "rich_html_enabled": True,
            "signature": {
                "enabled": True,
                "text": "  Generic assistant\nSupport  ",
                "html": "<strong>Generic assistant</strong>",
                "logo_width": 480,
            },
        }
        resp = client.put(
            "/api/messaging/platforms/email",
            params={"profile": "worker_alpha"},
            json={"config": email_config},
        )

        assert resp.status_code == 200
        worker_cfg = yaml.safe_load((worker_home / "config.yaml").read_text())
        assert worker_cfg["platforms"]["email"] == {
            "enabled": True,
            "poll_interval": 42,
            **email_config,
        }
        assert worker_cfg["platforms"]["telegram"] == {"enabled": False}
        root_cfg = yaml.safe_load(
            (isolated_profiles["default"] / "config.yaml").read_text()
        ) or {}
        assert "email" not in (root_cfg.get("platforms") or {})

        read = client.get(
            "/api/messaging/platforms", params={"profile": "worker_alpha"}
        )
        assert read.status_code == 200
        assert _email(read.json())["config"] == email_config

    @pytest.mark.parametrize("logo_width", [31, 1025, 230.5, "230", True])
    def test_email_signature_logo_width_rejects_invalid_api_values(
        self,
        client,
        isolated_profiles,
        logo_width,
    ):
        resp = client.put(
            "/api/messaging/platforms/email",
            params={"profile": "worker_alpha"},
            json={
                "config": {
                    "rich_html_enabled": True,
                    "signature": {
                        "enabled": True,
                        "text": "Canonical signature",
                        "html": "{{email_signature_logo}}",
                        "logo_width": logo_width,
                    },
                }
            },
        )

        assert resp.status_code == 422
        worker_cfg = yaml.safe_load(
            (isolated_profiles["worker_alpha"] / "config.yaml").read_text()
        ) or {}
        assert "email" not in (worker_cfg.get("platforms") or {})

    def test_email_signature_requires_plain_text_fallback(
        self, client, isolated_profiles
    ):
        resp = client.put(
            "/api/messaging/platforms/email",
            params={"profile": "worker_alpha"},
            json={
                "config": {
                    "rich_html_enabled": True,
                    "signature": {
                        "enabled": True,
                        "text": "   ",
                        "html": "<strong>HTML only</strong>",
                    },
                }
            },
        )

        assert resp.status_code == 400
        assert "signature.text" in resp.json()["detail"]
        worker_cfg = yaml.safe_load(
            (isolated_profiles["worker_alpha"] / "config.yaml").read_text()
        ) or {}
        assert "email" not in (worker_cfg.get("platforms") or {})

    def test_email_content_config_is_rejected_for_other_platforms(
        self, client, isolated_profiles
    ):
        resp = client.put(
            "/api/messaging/platforms/telegram",
            params={"profile": "worker_alpha"},
            json={
                "config": {
                    "rich_html_enabled": True,
                    "signature": {"enabled": False, "text": "", "html": ""},
                }
            },
        )

        assert resp.status_code == 400
        assert "Email" in resp.json()["detail"]

    def test_scoped_write_lands_in_target_profile_env(
        self, client, isolated_profiles
    ):
        resp = client.put(
            "/api/messaging/platforms/telegram",
            params={"profile": "worker_alpha"},
            json={
                "enabled": True,
                "env": {"TELEGRAM_BOT_TOKEN": _VALID_WORKER_BOT_TOKEN},
            },
        )
        assert resp.status_code == 200

        worker_env = (
            isolated_profiles["worker_alpha"] / ".env"
        ).read_text(encoding="utf-8")
        assert f"TELEGRAM_BOT_TOKEN={_VALID_WORKER_BOT_TOKEN}" in worker_env

        # The dashboard's own .env must stay untouched — this was the bug.
        root_env = (isolated_profiles["default"] / ".env").read_text(
            encoding="utf-8"
        )
        assert _VALID_WORKER_BOT_TOKEN not in root_env
        assert "TELEGRAM_BOT_TOKEN=root-token" in root_env

        # Enablement lands in the target profile's config.yaml.
        worker_cfg = yaml.safe_load(
            (isolated_profiles["worker_alpha"] / "config.yaml").read_text()
        ) or {}
        assert worker_cfg.get("platforms", {}).get("telegram", {}).get("enabled") is True
        root_cfg = yaml.safe_load(
            (isolated_profiles["default"] / "config.yaml").read_text()
        ) or {}
        assert "telegram" not in (root_cfg.get("platforms") or {})

    def test_scoped_read_after_scoped_write_round_trips(
        self, client, isolated_profiles
    ):
        client.put(
            "/api/messaging/platforms/telegram",
            params={"profile": "worker_alpha"},
            json={
                "enabled": True,
                "env": {"TELEGRAM_BOT_TOKEN": _VALID_WORKER_BOT_TOKEN},
            },
        )
        resp = client.get(
            "/api/messaging/platforms", params={"profile": "worker_alpha"}
        )
        telegram = _telegram(resp.json())
        assert telegram["enabled"] is True
        assert _env_field(telegram, "TELEGRAM_BOT_TOKEN")["is_set"] is True
        assert telegram["configured"] is True


class TestProfileScopedEmailSignatureLogo:
    def test_unknown_profile_uses_existing_messaging_404_resolution(
        self,
        client,
        isolated_profiles,
    ):
        response = client.get(
            "/api/messaging/email/signature-logo",
            params={"profile": "no_such_profile"},
        )

        assert response.status_code == 404

    def test_get_without_logo_returns_typed_empty_status(
        self,
        client,
        isolated_profiles,
    ):
        response = client.get(
            "/api/messaging/email/signature-logo",
            params={"profile": "worker_alpha"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "configured": False,
            "valid": False,
            "mime_type": None,
            "format": None,
            "size_bytes": None,
            "width": None,
            "height": None,
            "modified_at": None,
        }

    @pytest.mark.parametrize(
        ("image_format", "mime_type"),
        [
            ("PNG", "image/png"),
            ("JPEG", "image/jpeg"),
            ("GIF", "image/gif"),
            pytest.param(
                "WEBP",
                "image/webp",
                marks=pytest.mark.skipif(
                    not features.check("webp"),
                    reason="Pillow was built without WebP support",
                ),
            ),
        ],
    )
    def test_put_valid_image_uses_decoded_format_not_client_metadata(
        self,
        client,
        isolated_profiles,
        image_format,
        mime_type,
    ):
        data = _logo_bytes(image_format)

        response = _put_logo(client, data)

        assert response.status_code == 200
        payload = response.json()
        assert payload == {
            "configured": True,
            "valid": True,
            "mime_type": mime_type,
            "format": image_format,
            "size_bytes": len(data),
            "width": 12,
            "height": 8,
            "modified_at": payload["modified_at"],
        }
        assert isinstance(payload["modified_at"], str)

    @pytest.mark.parametrize(
        ("data", "expected_status"),
        [
            (b"not an image", 422),
            (_logo_bytes("PNG")[:-12], 422),
            (b"x" * (2 * 1024 * 1024 + 1), 413),
        ],
        ids=["non-image", "truncated", "over-2-mib"],
    )
    def test_put_rejects_invalid_content(
        self,
        client,
        isolated_profiles,
        data,
        expected_status,
    ):
        response = _put_logo(client, data)

        assert response.status_code == expected_status
        assert "signature logo" in response.json()["detail"].lower()

    def test_put_rejects_excessive_dimensions(
        self,
        client,
        isolated_profiles,
    ):
        response = _put_logo(client, _logo_bytes("PNG", size=(4097, 1)))

        assert response.status_code == 422
        assert "4096 x 4096" in response.json()["detail"]

    def test_invalid_put_preserves_previous_valid_logo(
        self,
        client,
        isolated_profiles,
    ):
        original = _logo_bytes("PNG")
        assert _put_logo(client, original).status_code == 200

        rejected = _put_logo(client, b"invalid replacement")
        status = client.get(
            "/api/messaging/email/signature-logo",
            params={"profile": "worker_alpha"},
        )

        assert rejected.status_code == 422
        assert status.status_code == 200
        assert status.json()["configured"] is True
        assert status.json()["valid"] is True
        assert status.json()["format"] == "PNG"
        stored = (
            isolated_profiles["worker_alpha"]
            / "assets"
            / "email"
            / "signature-logo.png"
        )
        assert stored.read_bytes() == original

    def test_put_format_change_removes_previous_canonical_file(
        self,
        client,
        isolated_profiles,
    ):
        assert _put_logo(client, _logo_bytes("PNG")).status_code == 200

        replacement = _put_logo(client, _logo_bytes("JPEG"))

        assert replacement.status_code == 200
        assert replacement.json()["format"] == "JPEG"
        asset_dir = isolated_profiles["worker_alpha"] / "assets" / "email"
        assert sorted(path.name for path in asset_dir.iterdir()) == [
            "signature-logo.jpg"
        ]

    def test_delete_existing_and_absent_logo_is_idempotent(
        self,
        client,
        isolated_profiles,
    ):
        assert _put_logo(client, _logo_bytes("PNG")).status_code == 200

        first = client.delete(
            "/api/messaging/email/signature-logo",
            params={"profile": "worker_alpha"},
        )
        second = client.delete(
            "/api/messaging/email/signature-logo",
            params={"profile": "worker_alpha"},
        )

        assert first.status_code == second.status_code == 200
        assert first.json()["configured"] is False
        assert second.json()["configured"] is False
        assert first.json()["valid"] is False
        assert second.json()["valid"] is False

    def test_upload_get_and_delete_are_isolated_between_profiles(
        self,
        client,
        isolated_profiles,
    ):
        worker_upload = _put_logo(client, _logo_bytes("JPEG"))
        assert worker_upload.status_code == 200

        default_before = client.get(
            "/api/messaging/email/signature-logo",
            params={"profile": "default"},
        )
        default_delete = client.delete(
            "/api/messaging/email/signature-logo",
            params={"profile": "default"},
        )
        worker_after = client.get(
            "/api/messaging/email/signature-logo",
            params={"profile": "worker_alpha"},
        )

        assert default_before.status_code == 200
        assert default_before.json()["configured"] is False
        assert default_delete.status_code == 200
        assert default_delete.json()["configured"] is False
        assert worker_after.status_code == 200
        assert worker_after.json()["configured"] is True
        assert worker_after.json()["format"] == "JPEG"
        assert not (
            isolated_profiles["default"] / "assets" / "email"
        ).exists()

    def test_response_never_exposes_profile_path_filename_content_or_cid(
        self,
        client,
        isolated_profiles,
    ):
        response = _put_logo(client, _logo_bytes("PNG"))

        assert response.status_code == 200
        payload_text = response.text
        assert str(isolated_profiles["worker_alpha"]) not in payload_text
        for forbidden in ("path", "filename", "content", "cid", "secret"):
            assert forbidden not in response.json()

    def test_storage_error_returns_safe_500_without_exception_path(
        self,
        client,
        isolated_profiles,
        monkeypatch,
    ):
        import hermes_cli.web_routers.messaging as messaging_router
        from plugins.platforms.email.assets import SignatureLogoStorageError

        secret_path = isolated_profiles["worker_alpha"] / "private" / "logo.png"

        def fail_save(data):
            raise SignatureLogoStorageError(f"cannot write {secret_path}")

        monkeypatch.setattr(messaging_router, "save_signature_logo", fail_save)

        response = _put_logo(client, _logo_bytes("PNG"))

        assert response.status_code == 500
        assert response.json() == {
            "detail": "Could not store Email signature logo"
        }
        assert str(secret_path) not in response.text


class TestProfileScopedEmailPreview:
    @staticmethod
    def _config(*, rich=True, enabled=True, html="<p>Signature</p>"):
        return {
            "rich_html_enabled": rich,
            "signature": {
                "enabled": enabled,
                "text": "Plain signature",
                "html": html,
                "logo_width": 230,
            },
        }

    def test_plain_only_preview_uses_canonical_fallback(self, client):
        response = _preview(
            client,
            self._config(rich=False),
            body="Hello **there**.",
        )

        assert response.status_code == 200
        assert response.json() == {
            "plain_text": "Hello **there**.\n\nPlain signature",
            "html": None,
            "resources": [],
        }

    def test_rich_preview_is_sanitized_and_has_no_remote_resources(self, client):
        response = _preview(
            client,
            self._config(
                html=(
                    '<div onclick="alert(1)">Signature'
                    '<img src="https://example.invalid/tracker.png">'
                    "<script>alert(1)</script></div>"
                )
            ),
            body='Hello <img src="cid:user-controlled"> **there**.',
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["plain_text"] == (
            'Hello <img src="cid:user-controlled"> **there**.\n\nPlain signature'
        )
        assert "<strong>there</strong>" in payload["html"]
        for forbidden in ("onclick", "script", "https://", "cid:user-controlled"):
            assert forbidden not in payload["html"]
        assert payload["resources"] == []

    def test_logo_token_returns_only_referenced_validated_resource(
        self,
        client,
        isolated_profiles,
    ):
        logo = _logo_bytes("PNG", size=(18, 7))
        assert _put_logo(client, logo).status_code == 200

        response = _preview(
            client,
            self._config(
                html=(
                    "<div>{{email_signature_logo}}"
                    "{{email_signature_logo}}</div>"
                )
            ),
        )

        assert response.status_code == 200
        payload = response.json()
        assert len(payload["resources"]) == 1
        resource = payload["resources"][0]
        assert resource == {
            "content_id": resource["content_id"],
            "mime_type": "image/png",
            "data_base64": base64.b64encode(logo).decode("ascii"),
            "size_bytes": len(logo),
            "width": 18,
            "height": 7,
        }
        assert payload["html"].count(f'cid:{resource["content_id"]}') == 2
        assert '{{email_signature_logo}}' not in payload["html"]
        assert str(isolated_profiles["worker_alpha"]) not in response.text
        assert "signature-logo.png" not in response.text

    def test_preview_matches_the_canonical_send_renderer_before_cid_conversion(
        self,
        client,
        monkeypatch,
    ):
        import plugins.platforms.email.mime as email_mime
        from hermes_cli.web_server_profiles import _profile_scope
        from plugins.platforms.email.rendering import (
            raw_signature_html,
            render_email_content,
            signature_from_extra,
            signature_logo_width_from_extra,
        )

        monkeypatch.setattr(
            email_mime,
            "make_msgid",
            lambda *, domain: f"<fixed-logo@{domain}>",
        )
        logo = _logo_bytes("PNG")
        assert _put_logo(client, logo).status_code == 200
        config = self._config(
            html="<div>{{email_signature_logo}}<strong>Signature</strong></div>"
        )
        config["signature"]["logo_width"] = 360

        response = _preview(client, config, body="Hello **there**.")
        with _profile_scope("worker_alpha"):
            expected = render_email_content(
                "Hello **there**.",
                rich_html_enabled=True,
                signature=signature_from_extra(config),
                raw_signature_html=raw_signature_html(config),
                logo_width=signature_logo_width_from_extra(config),
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["plain_text"] == expected.plain_text
        assert payload["html"] == expected.html
        assert len(expected.inline_images) == len(payload["resources"]) == 1
        assert payload["resources"][0]["content_id"] == (
            expected.inline_images[0].content_id
        )
        assert payload["resources"][0]["data_base64"] == base64.b64encode(
            expected.inline_images[0].content
        ).decode("ascii")
        assert 'width="360"' in payload["html"]
        assert "width:360px" in payload["html"]

    def test_text_signature_preview_derives_the_same_sanitized_html(self, client):
        response = _preview(
            client,
            self._config(html=""),
            body="Message",
        )

        assert response.status_code == 200
        assert response.json()["plain_text"] == "Message\n\nPlain signature"
        assert response.json()["html"].endswith(
            "\n<br>\n<p>Plain signature</p>"
        )
        assert response.json()["resources"] == []

    def test_missing_or_corrupt_logo_removes_token_without_failing(
        self,
        client,
        isolated_profiles,
    ):
        config = self._config(html="<div>{{email_signature_logo}}</div>")
        missing = _preview(client, config)
        assert missing.status_code == 200
        assert missing.json()["resources"] == []
        assert "email_signature_logo" not in missing.json()["html"]

        asset_dir = isolated_profiles["worker_alpha"] / "assets" / "email"
        asset_dir.mkdir(parents=True, exist_ok=True)
        (asset_dir / "signature-logo.png").write_bytes(b"corrupt")
        corrupt = _preview(client, config)
        assert corrupt.status_code == 200
        assert corrupt.json()["resources"] == []
        assert "email_signature_logo" not in corrupt.json()["html"]

    def test_preview_logo_is_profile_isolated(self, client):
        assert _put_logo(client, _logo_bytes("PNG")).status_code == 200
        config = self._config(html="{{email_signature_logo}}")

        worker = _preview(client, config, profile="worker_alpha")
        default = _preview(client, config, profile="default")

        assert worker.status_code == default.status_code == 200
        assert len(worker.json()["resources"]) == 1
        assert default.json()["resources"] == []

    def test_unknown_profile_and_invalid_signature_fail_safely(self, client):
        unknown = _preview(
            client,
            self._config(),
            profile="no_such_profile",
        )
        invalid = _preview(
            client,
            {
                "rich_html_enabled": True,
                "signature": {
                    "enabled": True,
                    "text": "",
                    "html": "<p>No fallback</p>",
                    "logo_width": 230,
                },
            },
        )

        assert unknown.status_code == 404
        assert invalid.status_code == 400
        assert "signature.text is required" in invalid.json()["detail"]

    def test_preview_request_size_is_bounded(self, client):
        response = _preview(
            client,
            self._config(),
            body="x" * 50_001,
        )

        assert response.status_code == 422

    @pytest.mark.parametrize(
        ("location", "field"),
        [
            ("request", "image_bytes"),
            ("config", "smtp_password"),
            ("signature", "content_id"),
        ],
    )
    def test_preview_rejects_unknown_or_secret_bearing_fields(
        self,
        client,
        location,
        field,
    ):
        payload = {
            "body_markdown": "Preview",
            "config": self._config(),
        }
        target = (
            payload
            if location == "request"
            else payload["config"]
            if location == "config"
            else payload["config"]["signature"]
        )
        target[field] = "must not be accepted"

        response = client.post(
            "/api/messaging/email/preview",
            params={"profile": "worker_alpha"},
            json=payload,
        )

        assert response.status_code == 422


def _enable_multiplex(default_home):
    (default_home / "config.yaml").write_text(
        yaml.safe_dump({"gateway": {"multiplex_profiles": True}}),
        encoding="utf-8",
    )


class TestMultiplexPortBindingGuard:
    """Enabling a port-binding channel on a secondary multiplexed profile
    must be rejected BEFORE anything is persisted.

    The gateway fail-fasts with ``MultiplexConfigError`` when a secondary
    profile enables a port-binding platform under
    ``gateway.multiplex_profiles`` — but the dashboard used to persist that
    exact config, so the next gateway start died for EVERY profile (#62791).
    """

    @pytest.fixture(autouse=True)
    def _no_multiplex_env_override(self, monkeypatch):
        # The operator env override must not leak into these tests: the
        # multiplex flag under test comes from the default profile's config.
        monkeypatch.delenv("GATEWAY_MULTIPLEX_PROFILES", raising=False)

    def test_rejects_every_port_binding_platform_on_secondary(
        self, client, isolated_profiles
    ):
        from gateway.config import PORT_BINDING_PLATFORM_VALUES

        _enable_multiplex(isolated_profiles["default"])
        assert PORT_BINDING_PLATFORM_VALUES  # guard set must not be empty
        for platform_id in sorted(PORT_BINDING_PLATFORM_VALUES):
            resp = client.put(
                f"/api/messaging/platforms/{platform_id}",
                params={"profile": "worker_alpha"},
                json={"enabled": True},
            )
            assert resp.status_code == 409, platform_id
            assert "default profile" in resp.json()["detail"]





    def test_secondary_can_disable_and_clear_invalid_config(
        self, client, isolated_profiles
    ):
        _enable_multiplex(isolated_profiles["default"])
        worker_home = isolated_profiles["worker_alpha"]
        (worker_home / "config.yaml").write_text(
            yaml.safe_dump({"platforms": {"api_server": {"enabled": True}}}),
            encoding="utf-8",
        )

        resp = client.put(
            "/api/messaging/platforms/api_server",
            params={"profile": "worker_alpha"},
            json={"enabled": False},
        )
        assert resp.status_code == 200
        cfg = yaml.safe_load((worker_home / "config.yaml").read_text())
        assert cfg["platforms"]["api_server"]["enabled"] is False

        catalog = client.get(
            "/api/messaging/platforms", params={"profile": "worker_alpha"}
        ).json()
        api_server = next(p for p in catalog["platforms"] if p["id"] == "api_server")
        if api_server["env_vars"]:
            resp = client.put(
                "/api/messaging/platforms/api_server",
                params={"profile": "worker_alpha"},
                json={"clear_env": [api_server["env_vars"][0]["key"]]},
            )
            assert resp.status_code == 200
