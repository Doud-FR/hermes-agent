"""Shared MIME construction for outgoing Email platform messages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email import encoders
from email.message import Message
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid
from html.parser import HTMLParser
from typing import Collection, Optional, Sequence

import markdown
import nh3


_MESSAGE_HTML_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}
_MESSAGE_HTML_ATTRIBUTES = {"a": {"href", "title"}}
_MESSAGE_HTML_CLEAN_CONTENT_TAGS = {
    "embed",
    "iframe",
    "noscript",
    "object",
    "script",
    "style",
    "template",
}
_MESSAGE_HTML_URL_SCHEMES = {"http", "https", "mailto"}

_SIGNATURE_HTML_TAGS = _MESSAGE_HTML_TAGS | {
    "div",
    "small",
    "span",
    "sub",
    "sup",
}
_SIGNATURE_HTML_ATTRIBUTES = {
    "a": {"href", "style", "title"},
    "div": {"style"},
    "p": {"style"},
    "small": {"style"},
    "span": {"style"},
    "table": {"border", "cellpadding", "cellspacing", "role", "style", "width"},
    "td": {"align", "colspan", "rowspan", "style", "valign", "width"},
    "th": {"align", "colspan", "rowspan", "style", "valign", "width"},
}
_SIGNATURE_HTML_STYLE_PROPERTIES = {
    "border",
    "border-bottom",
    "border-color",
    "border-left",
    "border-right",
    "border-style",
    "border-top",
    "border-width",
    "color",
    "display",
    "font-family",
    "font-size",
    "font-style",
    "font-weight",
    "height",
    "line-height",
    "margin",
    "margin-bottom",
    "margin-left",
    "margin-right",
    "margin-top",
    "max-width",
    "padding",
    "padding-bottom",
    "padding-left",
    "padding-right",
    "padding-top",
    "text-align",
    "text-decoration",
    "vertical-align",
    "white-space",
    "width",
}
_SIGNATURE_HTML_URL_SCHEMES = _MESSAGE_HTML_URL_SCHEMES | {"tel"}
_MIME_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+$")


def _signature_html_attribute_filter(
    tag: str,
    attribute: str,
    value: str,
) -> Optional[str]:
    """Keep inline-image attributes limited to the backend-generated set."""
    if tag == "img" and attribute not in {"alt", "src", "style", "width"}:
        return None
    return value


def _cid_sanitizer_options(
    allowed_cids: Collection[str],
    *,
    attributes: dict[str, set[str]],
    image_attributes: set[str],
) -> tuple[
    set[str],
    dict[str, set[str]],
    set[str],
    Optional[dict[str, dict[str, set[str]]]],
]:
    allowed_sources = {f"cid:{content_id}" for content_id in allowed_cids}
    if not allowed_sources:
        return set(), attributes, set(), None

    attributes_with_image = dict(attributes)
    attributes_with_image["img"] = image_attributes
    return (
        {"img"},
        attributes_with_image,
        {"cid"},
        {"img": {"src": allowed_sources}},
    )


def sanitize_message_html(
    html: str,
    *,
    allowed_cids: Collection[str] = (),
) -> str:
    """Sanitize rendered message HTML using the Email message policy."""
    image_tags, attributes, image_schemes, cid_values = _cid_sanitizer_options(
        allowed_cids,
        attributes=_MESSAGE_HTML_ATTRIBUTES,
        image_attributes={"alt", "height", "title", "width"},
    )
    return nh3.clean(
        html,
        tags=_MESSAGE_HTML_TAGS | image_tags,
        clean_content_tags=_MESSAGE_HTML_CLEAN_CONTENT_TAGS,
        attributes=attributes,
        tag_attribute_values=cid_values,
        strip_comments=True,
        link_rel="noopener noreferrer",
        url_schemes=_MESSAGE_HTML_URL_SCHEMES | image_schemes,
        url_relative="deny",
    )


def render_markdown_html(
    text: str,
    *,
    allowed_cids: Collection[str] = (),
) -> str:
    """Render Markdown and sanitize the resulting Email HTML fragment."""
    rendered = markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "sane_lists"],
        output_format="html",
    )
    return sanitize_message_html(rendered, allowed_cids=allowed_cids)


def sanitize_signature_html(
    html: str,
    *,
    allowed_cids: Collection[str] = (),
) -> str:
    """Sanitize signature HTML using its separate layout-oriented policy."""
    image_tags, attributes, image_schemes, cid_values = _cid_sanitizer_options(
        allowed_cids,
        attributes=_SIGNATURE_HTML_ATTRIBUTES,
        image_attributes={"alt", "style", "width"},
    )
    return nh3.clean(
        html,
        tags=_SIGNATURE_HTML_TAGS | image_tags,
        clean_content_tags=_MESSAGE_HTML_CLEAN_CONTENT_TAGS,
        attributes=attributes,
        tag_attribute_values=cid_values,
        strip_comments=True,
        link_rel="noopener noreferrer",
        url_schemes=_SIGNATURE_HTML_URL_SCHEMES | image_schemes,
        attribute_filter=_signature_html_attribute_filter,
        filter_style_properties=_SIGNATURE_HTML_STYLE_PROPERTIES,
        url_relative="deny",
    )


def _render_signature_text_html(
    text: str,
    *,
    allowed_cids: Collection[str] = (),
) -> str:
    rendered = markdown.markdown(
        text,
        extensions=["nl2br", "sane_lists"],
        output_format="html",
    )
    return sanitize_signature_html(rendered, allowed_cids=allowed_cids)


@dataclass(frozen=True)
class MimeAttachment:
    """An attachment payload ready to be added to an outgoing message."""

    filename: str
    content: bytes


def _generate_content_id() -> str:
    return make_msgid(domain="inline.invalid")[1:-1]


@dataclass(frozen=True)
class MimeInlineImage:
    """An inline image with a backend-generated MIME Content-ID."""

    filename: str
    content: bytes
    content_type: str
    content_id: str = field(default_factory=_generate_content_id, init=False)

def _validate_attachment_filename(filename: str) -> None:
    if any(character in filename for character in ("\x00", "\r", "\n")):
        raise ValueError(
            "attachment filename must not contain NUL, CR, or LF characters"
        )


@dataclass(frozen=True)
class MimeSignature:
    """Validated plain-text and sanitized HTML signature variants."""

    text: str
    html: str


def prepare_signature(
    *,
    enabled: bool,
    text: Optional[str] = None,
    html: Optional[str] = None,
    allowed_cids: Collection[str] = (),
) -> Optional[MimeSignature]:
    """Validate and prepare a configured Email signature."""
    if not enabled:
        return None
    if not isinstance(text, str) or not text.strip():
        raise ValueError(
            "email signature.text is required when signature.enabled is true"
        )
    if html is not None and not isinstance(html, str):
        raise ValueError("email signature.html must be a string when provided")

    if html and html.strip():
        sanitized_html = sanitize_signature_html(html, allowed_cids=allowed_cids)
        if not sanitized_html.strip():
            sanitized_html = _render_signature_text_html(
                text,
                allowed_cids=allowed_cids,
            )
    else:
        sanitized_html = _render_signature_text_html(
            text,
            allowed_cids=allowed_cids,
        )
    return MimeSignature(text=text, html=sanitized_html)


class _CidReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.content_ids: set[str] = set()

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, Optional[str]]],
    ) -> None:
        if tag != "img":
            return
        for name, value in attrs:
            if name == "src" and value is not None and value.startswith("cid:"):
                self.content_ids.add(value.removeprefix("cid:"))

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, Optional[str]]],
    ) -> None:
        self.handle_starttag(tag, attrs)


def _referenced_content_ids(html: str) -> set[str]:
    parser = _CidReferenceParser()
    parser.feed(html)
    parser.close()
    return parser.content_ids


def _inline_image_content_type(content_type: str) -> tuple[str, str]:
    maintype, separator, subtype = content_type.partition("/")
    if (
        separator != "/"
        or maintype.lower() != "image"
        or not _MIME_TOKEN_PATTERN.fullmatch(subtype)
    ):
        raise ValueError("inline image content_type must be a valid image/* MIME type")
    return "image", subtype.lower()


def _build_inline_image_part(image: MimeInlineImage) -> Message:
    _validate_attachment_filename(image.filename)
    maintype, subtype = _inline_image_content_type(image.content_type)
    part = MIMEBase(maintype, subtype)
    part.set_payload(image.content)
    encoders.encode_base64(part)
    part["Content-ID"] = f"<{image.content_id}>"
    part.add_header(
        "Content-Disposition",
        "inline",
        filename=image.filename,
    )
    return part


def compose_email_bodies(
    body: str,
    *,
    html_body: Optional[str] = None,
    signature: Optional[MimeSignature] = None,
) -> tuple[str, Optional[str]]:
    """Compose the canonical plain and HTML bodies before MIME construction."""
    plain_body = body
    effective_html_body = html_body
    if signature is not None:
        plain_body = f"{body}\n\n{signature.text}" if body else signature.text
        if html_body is not None:
            effective_html_body = (
                f"{html_body}\n<br>\n{signature.html}" if html_body else signature.html
            )
    return plain_body, effective_html_body


def build_email_message(
    *,
    from_address: str,
    to_address: str,
    subject: str,
    body: str,
    date: str,
    message_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
    attachments: Sequence[MimeAttachment] = (),
    inline_images: Sequence[MimeInlineImage] = (),
    html_body: Optional[str] = None,
    signature: Optional[MimeSignature] = None,
    force_multipart: bool = False,
    include_empty_body: bool = True,
) -> Message:
    """Build an outgoing message while preserving the legacy plain-text MIME.

    ``force_multipart`` and ``include_empty_body`` encode two historical Email
    adapter behaviors: gateway replies always use ``multipart/mixed``, while
    attachment sends omit an empty text part.  The standalone sender remains a
    direct ``text/plain`` message when it has no attachments.
    """
    plain_body, effective_html_body = compose_email_bodies(
        body,
        html_body=html_body,
        signature=signature,
    )

    include_body = bool(plain_body) or include_empty_body
    alternative = None
    related = None
    referenced_inline_images: tuple[MimeInlineImage, ...] = ()
    if effective_html_body is not None and include_body:
        content_ids = [image.content_id for image in inline_images]
        if len(content_ids) != len(set(content_ids)):
            raise ValueError("inline image Content-IDs must be unique")
        referenced_content_ids = _referenced_content_ids(effective_html_body)
        referenced_inline_images = tuple(
            image
            for image in inline_images
            if image.content_id in referenced_content_ids
        )
        alternative = MIMEMultipart("alternative")
        if attachments:
            message = MIMEMultipart()
            if referenced_inline_images:
                related = MIMEMultipart(
                    "related",
                    type="multipart/alternative",
                )
                message.attach(related)
                related.attach(alternative)
        elif referenced_inline_images:
            related = MIMEMultipart(
                "related",
                type="multipart/alternative",
            )
            message = related
            related.attach(alternative)
        else:
            message = alternative
    elif force_multipart or attachments:
        message: Message = MIMEMultipart()
    else:
        message = MIMEText(plain_body, "plain", "utf-8")

    message["From"] = from_address
    message["To"] = to_address
    message["Subject"] = subject
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    message["Date"] = date
    if message_id:
        message["Message-ID"] = message_id

    if alternative is not None:
        alternative.attach(MIMEText(plain_body, "plain", "utf-8"))
        alternative.attach(MIMEText(effective_html_body, "html", "utf-8"))
        if message is not alternative and related is None:
            message.attach(alternative)
    elif message.is_multipart():
        if include_body:
            message.attach(MIMEText(plain_body, "plain", "utf-8"))

    if related is not None:
        for inline_image in referenced_inline_images:
            related.attach(_build_inline_image_part(inline_image))

    for attachment in attachments:
        _validate_attachment_filename(attachment.filename)
        part = MIMEBase("application", "octet-stream")
        part.set_payload(attachment.content)
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            "attachment",
            filename=attachment.filename,
        )
        message.attach(part)

    return message
