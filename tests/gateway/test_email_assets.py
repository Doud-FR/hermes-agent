"""Profile-scoped storage contract for Email signature logo assets."""

from __future__ import annotations

from dataclasses import asdict
from io import BytesIO
import inspect
import os
from pathlib import Path

from PIL import Image, features
import pytest

from plugins.platforms.email import assets as email_assets
from plugins.platforms.email.assets import (
    MAX_SIGNATURE_LOGO_BYTES,
    SignatureLogoStorageError,
    SignatureLogoValidationError,
    delete_signature_logo,
    get_signature_logo,
    get_signature_logo_status,
    load_signature_logo_inline_image,
    save_signature_logo,
    validate_signature_logo,
)
from plugins.platforms.email.mime import build_email_message


_DATE = "Mon, 24 Aug 2026 10:00:00 +0200"


@pytest.fixture(autouse=True)
def _isolated_hermes_home(monkeypatch, tmp_path: Path):
    home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _image_bytes(
    image_format: str,
    *,
    size: tuple[int, int] = (12, 8),
    color: str = "blue",
    animated: bool = False,
) -> bytes:
    mode = "RGB" if image_format == "JPEG" else "RGBA"
    first = Image.new(mode, size, color=color)
    output = BytesIO()
    if animated:
        second = Image.new(mode, size, color="green")
        first.save(
            output,
            format=image_format,
            save_all=True,
            append_images=[second],
            duration=100,
            loop=0,
        )
    else:
        first.save(output, format=image_format)
    return output.getvalue()


@pytest.mark.parametrize(
    ("image_format", "mime_type", "extension", "animated"),
    [
        ("PNG", "image/png", ".png", False),
        ("JPEG", "image/jpeg", ".jpg", False),
        ("GIF", "image/gif", ".gif", True),
        pytest.param(
            "WEBP",
            "image/webp",
            ".webp",
            True,
            marks=pytest.mark.skipif(
                not features.check("webp"),
                reason="Pillow was built without WebP support",
            ),
        ),
    ],
)
def test_valid_formats_use_canonical_metadata_and_filename(
    _isolated_hermes_home: Path,
    image_format: str,
    mime_type: str,
    extension: str,
    animated: bool,
):
    data = _image_bytes(image_format, animated=animated)

    validated = validate_signature_logo(data)
    saved = save_signature_logo(data)
    asset = get_signature_logo()

    assert validated.format == saved.format == image_format
    assert validated.mime_type == saved.mime_type == mime_type
    assert (saved.width, saved.height) == (12, 8)
    assert saved.size_bytes == len(data)
    assert asset is not None
    assert asset.data == data
    assert asset.metadata == saved
    expected = (
        _isolated_hermes_home
        / "assets"
        / "email"
        / f"signature-logo{extension}"
    )
    assert expected.read_bytes() == data


def test_filename_or_declared_content_type_cannot_influence_detected_format(
    _isolated_hermes_home: Path,
):
    png = _image_bytes("PNG")

    assert tuple(inspect.signature(save_signature_logo).parameters) == ("data",)
    metadata = save_signature_logo(png)

    assert metadata.format == "PNG"
    assert metadata.mime_type == "image/png"
    assert (
        _isolated_hermes_home / "assets" / "email" / "signature-logo.png"
    ).exists()
    assert not (
        _isolated_hermes_home / "assets" / "email" / "signature-logo.jpg"
    ).exists()


@pytest.mark.parametrize(
    "data",
    [
        b"not an image",
        _image_bytes("PNG")[:-12],
    ],
    ids=["non-image", "truncated"],
)
def test_invalid_or_truncated_content_is_rejected(data: bytes):
    with pytest.raises(SignatureLogoValidationError):
        validate_signature_logo(data)


def test_truncated_animated_gif_is_rejected_without_decoding_every_frame():
    animated_gif = _image_bytes("GIF", animated=True)

    with pytest.raises(SignatureLogoValidationError, match="GIF container"):
        validate_signature_logo(animated_gif[:-1])


def test_raw_size_limit_is_enforced_before_image_decoding():
    oversized = b"x" * (MAX_SIGNATURE_LOGO_BYTES + 1)

    with pytest.raises(SignatureLogoValidationError, match="2 MiB"):
        validate_signature_logo(oversized)


def test_width_and_height_limits_are_enforced():
    too_wide = _image_bytes("PNG", size=(4097, 1))
    too_tall = _image_bytes("PNG", size=(1, 4097))

    with pytest.raises(SignatureLogoValidationError, match="4096 x 4096"):
        validate_signature_logo(too_wide)
    with pytest.raises(SignatureLogoValidationError, match="4096 x 4096"):
        validate_signature_logo(too_tall)


def test_total_pixel_limit_is_enforced_independently_of_dimensions():
    image = Image.new("1", (4001, 4000))
    output = BytesIO()
    image.save(output, format="PNG")

    with pytest.raises(SignatureLogoValidationError, match="16,000,000 pixels"):
        validate_signature_logo(output.getvalue())


def test_same_format_replacement_uses_same_directory_atomic_replace(
    monkeypatch,
    _isolated_hermes_home: Path,
):
    original = _image_bytes("PNG", color="blue")
    replacement = _image_bytes("PNG", color="red")
    save_signature_logo(original)
    calls: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def recording_replace(source, target):
        calls.append((Path(source), Path(target)))
        return real_replace(source, target)

    monkeypatch.setattr(email_assets.os, "replace", recording_replace)

    save_signature_logo(replacement)

    assert len(calls) == 1
    source, target = calls[0]
    assert source.parent == target.parent
    assert source.name.startswith(".signature-logo-")
    assert target.name == "signature-logo.png"
    assert target.read_bytes() == replacement


def test_format_change_replaces_then_removes_previous_canonical_file(
    _isolated_hermes_home: Path,
):
    save_signature_logo(_image_bytes("PNG"))
    asset_dir = _isolated_hermes_home / "assets" / "email"

    saved = save_signature_logo(_image_bytes("JPEG"))

    assert saved.format == "JPEG"
    assert not (asset_dir / "signature-logo.png").exists()
    assert (asset_dir / "signature-logo.jpg").exists()
    assert [path.name for path in asset_dir.iterdir()] == ["signature-logo.jpg"]


def test_invalid_replacement_never_destroys_existing_logo(
    _isolated_hermes_home: Path,
):
    original = _image_bytes("PNG")
    save_signature_logo(original)

    with pytest.raises(SignatureLogoValidationError):
        save_signature_logo(b"invalid replacement")

    target = _isolated_hermes_home / "assets" / "email" / "signature-logo.png"
    assert target.read_bytes() == original
    assert get_signature_logo_status().valid is True


def test_disk_replace_failure_is_explicit_and_preserves_existing_logo(
    monkeypatch,
    _isolated_hermes_home: Path,
):
    original = _image_bytes("PNG", color="blue")
    save_signature_logo(original)

    def denied_replace(source, target):
        raise PermissionError("denied")

    monkeypatch.setattr(email_assets.os, "replace", denied_replace)

    with pytest.raises(SignatureLogoStorageError, match="replace"):
        save_signature_logo(_image_bytes("PNG", color="red"))

    target = _isolated_hermes_home / "assets" / "email" / "signature-logo.png"
    assert target.read_bytes() == original


def test_delete_is_idempotent_and_reports_whether_an_asset_existed():
    save_signature_logo(_image_bytes("PNG"))

    assert delete_signature_logo() is True
    assert delete_signature_logo() is False
    assert get_signature_logo() is None
    assert get_signature_logo_status().configured is False


def test_no_configured_asset_has_safe_empty_status():
    status = get_signature_logo_status()

    assert asdict(status) == {
        "configured": False,
        "valid": False,
        "mime_type": None,
        "format": None,
        "size_bytes": None,
        "width": None,
        "height": None,
        "modified_at": None,
    }


def test_storage_is_isolated_by_active_hermes_home(monkeypatch, tmp_path: Path):
    first_home = tmp_path / "first-profile"
    second_home = tmp_path / "second-profile"
    monkeypatch.setenv("HERMES_HOME", str(first_home))
    save_signature_logo(_image_bytes("PNG"))

    monkeypatch.setenv("HERMES_HOME", str(second_home))
    assert get_signature_logo() is None
    save_signature_logo(_image_bytes("JPEG"))

    monkeypatch.setenv("HERMES_HOME", str(first_home))
    assert get_signature_logo().metadata.format == "PNG"
    monkeypatch.setenv("HERMES_HOME", str(second_home))
    assert get_signature_logo().metadata.format == "JPEG"


def test_public_metadata_and_asset_never_expose_a_filesystem_path(tmp_path: Path):
    metadata = save_signature_logo(_image_bytes("PNG"))
    asset = get_signature_logo()
    status = get_signature_logo_status()

    assert asset is not None
    for public_value in (metadata, asset.metadata, status):
        values = asdict(public_value)
        assert "path" not in values
        assert str(tmp_path) not in repr(values)
    assert "path" not in asdict(asset)


def test_asset_directory_symlink_escape_is_rejected_when_supported(
    _isolated_hermes_home: Path,
    tmp_path: Path,
):
    outside = tmp_path / "outside"
    outside.mkdir()
    assets_dir = _isolated_hermes_home / "assets"
    assets_dir.mkdir(parents=True)
    email_dir = assets_dir / "email"
    try:
        email_dir.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    with pytest.raises(SignatureLogoStorageError, match="symlink|outside"):
        save_signature_logo(_image_bytes("PNG"))
    assert list(outside.iterdir()) == []


def test_valid_asset_loads_as_backend_generated_mime_inline_image():
    data = _image_bytes("WEBP") if features.check("webp") else _image_bytes("PNG")
    metadata = save_signature_logo(data)

    inline_image = load_signature_logo_inline_image(
        signature_enabled=True,
        rich_html_enabled=True,
    )

    assert inline_image is not None
    assert inline_image.content == data
    assert inline_image.content_type == metadata.mime_type
    assert inline_image.filename == f"signature-logo{metadata.extension}"
    assert inline_image.content_id.endswith("@inline.invalid")


def test_absent_or_corrupt_logo_is_omitted_without_blocking_email(
    caplog,
    _isolated_hermes_home: Path,
):
    assert load_signature_logo_inline_image(
        signature_enabled=True,
        rich_html_enabled=True,
    ) is None
    asset_dir = _isolated_hermes_home / "assets" / "email"
    asset_dir.mkdir(parents=True)
    (asset_dir / "signature-logo.png").write_bytes(b"corrupt")

    assert load_signature_logo_inline_image(
        signature_enabled=True,
        rich_html_enabled=True,
    ) is None
    message = build_email_message(
        from_address="hermes@test.com",
        to_address="user@test.com",
        subject="Graceful fallback",
        body="Plain fallback",
        date=_DATE,
        html_body="<p>HTML fallback</p>",
        inline_images=(),
    )

    assert message.get_content_type() == "multipart/alternative"
    assert "Ignoring invalid Email signature logo" in caplog.text


def test_logo_loading_is_disabled_without_signature_and_rich_html(
    monkeypatch,
):
    loader = pytest.fail
    monkeypatch.setattr(email_assets, "get_signature_logo", loader)

    assert load_signature_logo_inline_image(
        signature_enabled=False,
        rich_html_enabled=True,
    ) is None
    assert load_signature_logo_inline_image(
        signature_enabled=True,
        rich_html_enabled=False,
    ) is None
