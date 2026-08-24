"""Canonical rendering pipeline for outgoing Email content and previews."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Optional

from plugins.platforms.email.assets import load_signature_logo_inline_image
from plugins.platforms.email.mime import (
    MimeInlineImage,
    MimeSignature,
    compose_email_bodies,
    prepare_signature,
    render_markdown_html,
)
from utils import is_truthy_value


EMAIL_SIGNATURE_LOGO_TOKEN = "{{email_signature_logo}}"
EMAIL_SIGNATURE_LOGO_WIDTH_DEFAULT = 230
EMAIL_SIGNATURE_LOGO_WIDTH_MIN = 32
EMAIL_SIGNATURE_LOGO_WIDTH_MAX = 1024

SignatureLogoLoader = Callable[..., Optional[MimeInlineImage]]
MarkdownRenderer = Callable[[str], str]


@dataclass(frozen=True)
class RenderedEmailContent:
    """Final canonical Email bodies and their referenced inline resources."""

    plain_text: str
    html: Optional[str]
    inline_images: tuple[MimeInlineImage, ...]


def signature_from_extra(extra: Mapping[str, Any]) -> Optional[MimeSignature]:
    """Read and validate the optional backend Email signature config."""
    signature = extra.get("signature")
    if signature is None:
        return None
    if not isinstance(signature, Mapping):
        raise ValueError("email signature must be a mapping")
    return prepare_signature(
        enabled=is_truthy_value(signature.get("enabled"), default=False),
        text=signature.get("text"),
        html=signature.get("html"),
    )


def raw_signature_html(extra: Mapping[str, Any]) -> Optional[str]:
    """Return the configured unsanitized signature HTML for token expansion."""
    signature = extra.get("signature")
    if not isinstance(signature, Mapping):
        return None
    html = signature.get("html")
    return html if isinstance(html, str) else None


def signature_logo_width_from_extra(extra: Mapping[str, Any]) -> int:
    """Read and validate the backend-controlled signature logo width."""
    signature = extra.get("signature")
    if not isinstance(signature, Mapping) or "logo_width" not in signature:
        return EMAIL_SIGNATURE_LOGO_WIDTH_DEFAULT

    logo_width = signature["logo_width"]
    if (
        isinstance(logo_width, bool)
        or not isinstance(logo_width, int)
        or not EMAIL_SIGNATURE_LOGO_WIDTH_MIN
        <= logo_width
        <= EMAIL_SIGNATURE_LOGO_WIDTH_MAX
    ):
        raise ValueError(
            "email signature.logo_width must be an integer between "
            f"{EMAIL_SIGNATURE_LOGO_WIDTH_MIN} and "
            f"{EMAIL_SIGNATURE_LOGO_WIDTH_MAX}"
        )
    return logo_width


def prepare_signature_delivery(
    signature: Optional[MimeSignature],
    *,
    raw_html: Optional[str],
    rich_html_enabled: bool,
    logo_width: int = EMAIL_SIGNATURE_LOGO_WIDTH_DEFAULT,
    logo_loader: SignatureLogoLoader = load_signature_logo_inline_image,
) -> tuple[Optional[MimeSignature], tuple[MimeInlineImage, ...]]:
    """Expand the controlled signature-logo token for one rendered message."""
    if (
        signature is None
        or not rich_html_enabled
        or raw_html is None
        or EMAIL_SIGNATURE_LOGO_TOKEN not in raw_html
    ):
        return signature, ()

    logo = logo_loader(
        signature_enabled=True,
        rich_html_enabled=True,
    )
    allowed_cids: tuple[str, ...] = ()
    inline_images: tuple[MimeInlineImage, ...] = ()
    replacement = ""
    if logo is not None:
        allowed_cids = (logo.content_id,)
        inline_images = (logo,)
        replacement = (
            f'<img src="cid:{logo.content_id}" alt="Signature logo" '
            f'width="{logo_width}" style="display:block; width:{logo_width}px; '
            'max-width:100%; height:auto; border:0;">'
        )

    expanded_html = raw_html.replace(EMAIL_SIGNATURE_LOGO_TOKEN, replacement)
    prepared = prepare_signature(
        enabled=True,
        text=signature.text,
        html=expanded_html,
        allowed_cids=allowed_cids,
    )
    return prepared, inline_images


def render_email_content(
    body_markdown: str,
    *,
    rich_html_enabled: bool,
    signature: Optional[MimeSignature],
    raw_signature_html: Optional[str],
    logo_width: int,
    logo_loader: SignatureLogoLoader = load_signature_logo_inline_image,
    markdown_renderer: MarkdownRenderer = render_markdown_html,
) -> RenderedEmailContent:
    """Render the exact plain/HTML content used by outgoing Email messages."""
    html_body = markdown_renderer(body_markdown) if rich_html_enabled else None
    prepared_signature, inline_images = prepare_signature_delivery(
        signature,
        raw_html=raw_signature_html,
        rich_html_enabled=rich_html_enabled,
        logo_width=logo_width,
        logo_loader=logo_loader,
    )
    plain_text, html = compose_email_bodies(
        body_markdown,
        html_body=html_body,
        signature=prepared_signature,
    )
    return RenderedEmailContent(
        plain_text=plain_text,
        html=html,
        inline_images=inline_images,
    )
