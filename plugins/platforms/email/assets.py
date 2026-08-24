"""Profile-scoped storage for Email signature logo assets."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from io import BytesIO
import logging
import os
from pathlib import Path
import stat
import tempfile
import warnings
from typing import Optional

from PIL import Image, UnidentifiedImageError

from hermes_constants import get_hermes_home

from .mime import MimeInlineImage


logger = logging.getLogger(__name__)

MAX_SIGNATURE_LOGO_BYTES = 2 * 1024 * 1024
MAX_SIGNATURE_LOGO_WIDTH = 4096
MAX_SIGNATURE_LOGO_HEIGHT = 4096
MAX_SIGNATURE_LOGO_PIXELS = 16_000_000

_ASSET_DIRECTORY_PARTS = ("assets", "email")
_FORMAT_DETAILS = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "GIF": ("image/gif", ".gif"),
    "WEBP": ("image/webp", ".webp"),
}


class SignatureLogoError(Exception):
    """Base error for signature logo validation and storage."""


class SignatureLogoValidationError(SignatureLogoError, ValueError):
    """Raised when supplied or stored bytes are not an acceptable image."""


class SignatureLogoStorageError(SignatureLogoError, OSError):
    """Raised when profile-scoped logo storage cannot be accessed safely."""


@dataclass(frozen=True)
class SignatureLogoMetadata:
    """Safe metadata for a validated signature logo, without its path."""

    mime_type: str
    format: str
    size_bytes: int
    width: int
    height: int
    modified_at: Optional[str] = None

    @property
    def extension(self) -> str:
        return _FORMAT_DETAILS[self.format][1]


@dataclass(frozen=True)
class SignatureLogoAsset:
    """Validated logo bytes and public metadata; no filesystem path."""

    data: bytes
    metadata: SignatureLogoMetadata


@dataclass(frozen=True)
class SignatureLogoStatus:
    """Safe status suitable for a future API response."""

    configured: bool
    valid: bool
    mime_type: Optional[str] = None
    format: Optional[str] = None
    size_bytes: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    modified_at: Optional[str] = None


def validate_signature_logo(data: bytes) -> SignatureLogoMetadata:
    """Validate image bytes using Pillow and return path-free metadata.

    Animated containers are verified, but only their first frame is decoded.
    This detects malformed input without needlessly expanding every frame.
    """
    if not isinstance(data, bytes):
        raise SignatureLogoValidationError("signature logo data must be bytes")
    if not data:
        raise SignatureLogoValidationError("signature logo must not be empty")
    if len(data) > MAX_SIGNATURE_LOGO_BYTES:
        raise SignatureLogoValidationError(
            "signature logo must not exceed 2 MiB"
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                image_format = (image.format or "").upper()
                width, height = image.size
                if image_format not in _FORMAT_DETAILS:
                    raise SignatureLogoValidationError(
                        "signature logo format must be PNG, JPEG, GIF, or WebP"
                    )
                if width <= 0 or height <= 0:
                    raise SignatureLogoValidationError(
                        "signature logo dimensions must be positive"
                    )
                if (
                    width > MAX_SIGNATURE_LOGO_WIDTH
                    or height > MAX_SIGNATURE_LOGO_HEIGHT
                ):
                    raise SignatureLogoValidationError(
                        "signature logo dimensions must not exceed 4096 x 4096"
                    )
                if width * height > MAX_SIGNATURE_LOGO_PIXELS:
                    raise SignatureLogoValidationError(
                        "signature logo must not exceed 16,000,000 pixels"
                    )
                if image_format == "GIF" and not data.endswith(b"\x3b"):
                    raise SignatureLogoValidationError(
                        "signature logo GIF container is truncated"
                    )
                image.verify()

            with Image.open(BytesIO(data)) as first_frame:
                first_frame.seek(0)
                first_frame.load()
                if (first_frame.format or "").upper() != image_format:
                    raise SignatureLogoValidationError(
                        "signature logo format changed while decoding"
                    )
                if first_frame.size != (width, height):
                    raise SignatureLogoValidationError(
                        "signature logo dimensions changed while decoding"
                    )
    except SignatureLogoValidationError:
        raise
    except (
        EOFError,
        Image.DecompressionBombError,
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        ValueError,
    ) as exc:
        raise SignatureLogoValidationError(
            "signature logo is not a valid, complete image"
        ) from exc

    mime_type, _ = _FORMAT_DETAILS[image_format]
    return SignatureLogoMetadata(
        mime_type=mime_type,
        format=image_format,
        size_bytes=len(data),
        width=width,
        height=height,
    )


def _storage_root() -> Path:
    try:
        root = get_hermes_home().expanduser().resolve(strict=False)
    except OSError as exc:
        raise SignatureLogoStorageError(
            "could not resolve the active HERMES_HOME"
        ) from exc
    return root


def _assert_confined(path: Path, root: Path) -> None:
    try:
        resolved = path.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise SignatureLogoStorageError(
            "Email signature logo storage resolves outside HERMES_HOME"
        ) from exc


def _asset_directory(*, create: bool) -> Optional[Path]:
    root = _storage_root()
    current = root
    if create:
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SignatureLogoStorageError(
                "could not create the active HERMES_HOME"
            ) from exc

    for part in _ASSET_DIRECTORY_PARTS:
        current = current / part
        if current.is_symlink():
            raise SignatureLogoStorageError(
                "Email signature logo storage must not use symlink directories"
            )
        if not current.exists():
            if not create:
                return None
            try:
                current.mkdir()
            except OSError as exc:
                raise SignatureLogoStorageError(
                    "could not create Email signature logo storage"
                ) from exc
        if not current.is_dir():
            raise SignatureLogoStorageError(
                "Email signature logo storage is not a directory"
            )
        _assert_confined(current, root)
    return current


def _canonical_paths(*, create: bool) -> tuple[Path, ...]:
    directory = _asset_directory(create=create)
    if directory is None:
        return ()
    return tuple(
        directory / f"signature-logo{extension}"
        for _, extension in _FORMAT_DETAILS.values()
    )


def _existing_logo_paths() -> tuple[Path, ...]:
    paths = _canonical_paths(create=False)
    existing: list[Path] = []
    root = _storage_root()
    for path in paths:
        if os.path.lexists(path):
            if path.is_symlink():
                raise SignatureLogoStorageError(
                    "Email signature logo must not be a symlink"
                )
            _assert_confined(path, root)
            if not path.is_file():
                raise SignatureLogoStorageError(
                    "Email signature logo storage entry is not a regular file"
                )
            existing.append(path)
    return tuple(existing)


def _read_file_no_follow(path: Path) -> tuple[bytes, os.stat_result]:
    root = _storage_root()
    if path.is_symlink():
        raise SignatureLogoStorageError(
            "Email signature logo must not be a symlink"
        )
    _assert_confined(path, root)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SignatureLogoStorageError(
            "could not open the Email signature logo safely"
        ) from exc
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise SignatureLogoStorageError(
                "Email signature logo is not a regular file"
            )
        chunks: list[bytes] = []
        remaining = MAX_SIGNATURE_LOGO_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks), file_stat
    finally:
        os.close(descriptor)


def _metadata_with_mtime(
    metadata: SignatureLogoMetadata,
    modified_timestamp: float,
) -> SignatureLogoMetadata:
    modified_at = datetime.fromtimestamp(
        modified_timestamp,
        tz=timezone.utc,
    ).isoformat()
    return replace(metadata, modified_at=modified_at)


def get_signature_logo() -> Optional[SignatureLogoAsset]:
    """Load and revalidate the active profile's signature logo."""
    paths = _existing_logo_paths()
    if not paths:
        return None
    if len(paths) != 1:
        raise SignatureLogoStorageError(
            "multiple Email signature logo files are configured"
        )
    data, file_stat = _read_file_no_follow(paths[0])
    metadata = _metadata_with_mtime(
        validate_signature_logo(data),
        file_stat.st_mtime,
    )
    return SignatureLogoAsset(data=data, metadata=metadata)


def get_signature_logo_status() -> SignatureLogoStatus:
    """Return safe status without exposing the profile filesystem path."""
    try:
        configured = bool(_existing_logo_paths())
        if not configured:
            return SignatureLogoStatus(configured=False, valid=False)
        asset = get_signature_logo()
    except SignatureLogoError as exc:
        logger.warning("Invalid Email signature logo storage: %s", exc)
        return SignatureLogoStatus(configured=True, valid=False)

    if asset is None:
        return SignatureLogoStatus(configured=False, valid=False)
    metadata = asset.metadata
    return SignatureLogoStatus(
        configured=True,
        valid=True,
        mime_type=metadata.mime_type,
        format=metadata.format,
        size_bytes=metadata.size_bytes,
        width=metadata.width,
        height=metadata.height,
        modified_at=metadata.modified_at,
    )


def _write_temporary_logo(directory: Path, data: bytes) -> Path:
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=directory,
            prefix=".signature-logo-",
            suffix=".tmp",
        )
    except OSError as exc:
        raise SignatureLogoStorageError(
            "could not create a temporary Email signature logo"
        ) from exc

    temporary_path = Path(temporary_name)
    write_succeeded = False
    try:
        try:
            os.fchmod(descriptor, 0o600)
        except (AttributeError, OSError):
            pass
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("temporary logo write made no progress")
            offset += written
        os.fsync(descriptor)
        write_succeeded = True
    except OSError as exc:
        raise SignatureLogoStorageError(
            "could not write the temporary Email signature logo"
        ) from exc
    finally:
        os.close(descriptor)
        if not write_succeeded:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
    return temporary_path


def save_signature_logo(data: bytes) -> SignatureLogoMetadata:
    """Validate and atomically store a logo for the active profile."""
    metadata = validate_signature_logo(data)
    directory = _asset_directory(create=True)
    assert directory is not None
    root = _storage_root()
    _assert_confined(directory, root)
    target = directory / f"signature-logo{metadata.extension}"
    existing = _existing_logo_paths()
    if target.is_symlink():
        raise SignatureLogoStorageError(
            "Email signature logo target must not be a symlink"
        )

    temporary_path = _write_temporary_logo(directory, data)
    replaced = False
    try:
        _assert_confined(directory, root)
        if directory.is_symlink():
            raise SignatureLogoStorageError(
                "Email signature logo storage must not use symlink directories"
            )
        try:
            os.replace(temporary_path, target)
            replaced = True
        except OSError as exc:
            raise SignatureLogoStorageError(
                "could not atomically replace the Email signature logo"
            ) from exc

        old_paths = [path for path in existing if path != target]
        try:
            for old_path in old_paths:
                if old_path.is_symlink():
                    raise SignatureLogoStorageError(
                        "previous Email signature logo must not be a symlink"
                    )
                old_path.unlink()
        except OSError as exc:
            if target not in existing:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
            raise SignatureLogoStorageError(
                "could not remove the previous Email signature logo"
            ) from exc
    finally:
        if not replaced:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    try:
        file_stat = target.stat()
    except OSError as exc:
        raise SignatureLogoStorageError(
            "could not inspect the saved Email signature logo"
        ) from exc
    return _metadata_with_mtime(metadata, file_stat.st_mtime)


def delete_signature_logo() -> bool:
    """Delete the active profile's canonical logo file, if present."""
    paths = _existing_logo_paths()
    if not paths:
        return False
    for path in paths:
        try:
            path.unlink()
        except OSError as exc:
            raise SignatureLogoStorageError(
                "could not delete the Email signature logo"
            ) from exc
    return True


def load_signature_logo_inline_image(
    *,
    signature_enabled: bool,
    rich_html_enabled: bool,
) -> Optional[MimeInlineImage]:
    """Load a valid logo as a MIME image, or omit it gracefully."""
    if not signature_enabled or not rich_html_enabled:
        return None
    try:
        asset = get_signature_logo()
    except SignatureLogoError as exc:
        logger.warning("Ignoring invalid Email signature logo: %s", exc)
        return None
    if asset is None:
        return None
    return MimeInlineImage(
        filename=f"signature-logo{asset.metadata.extension}",
        content=asset.data,
        content_type=asset.metadata.mime_type,
    )
