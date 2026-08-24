"""Behavioral contract tests for the Email platform's outgoing MIME messages."""

from __future__ import annotations

import asyncio
from email import policy
from email import utils as email_utils
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.email import adapter as email_adapter
from plugins.platforms.email.mime import (
    MimeAttachment,
    MimeInlineImage,
    MimeSignature,
    build_email_message,
    render_markdown_html,
    sanitize_message_html,
    sanitize_signature_html,
)
from plugins.platforms.email.rendering import render_email_content


_DATE = "Sat, 23 Aug 2026 10:00:00 +0200"
_MESSAGE_ID = "<hermes-0123456789ab@test.com>"


def test_common_renderer_preserves_historical_plain_and_html_composition():
    body = "Hello **there**."
    signature = MimeSignature(
        text="Hermes Agent\nInternal assistant",
        html="<p><strong>Hermes Agent</strong><br>Internal assistant</p>",
    )

    rendered = render_email_content(
        body,
        rich_html_enabled=True,
        signature=signature,
        raw_signature_html=signature.html,
        logo_width=230,
    )

    assert rendered.plain_text == (
        "Hello **there**.\n\nHermes Agent\nInternal assistant"
    )
    assert rendered.html == (
        f"{render_markdown_html(body)}\n<br>\n{signature.html}"
    )
    assert rendered.inline_images == ()


def test_common_renderer_preserves_historical_plain_only_content():
    rendered = render_email_content(
        "Plain **Markdown**.",
        rich_html_enabled=False,
        signature=None,
        raw_signature_html=None,
        logo_width=230,
    )

    assert rendered.plain_text == "Plain **Markdown**."
    assert rendered.html is None
    assert rendered.inline_images == ()


def _make_adapter(monkeypatch, extra=None) -> email_adapter.EmailAdapter:
    monkeypatch.setenv("EMAIL_ADDRESS", "hermes@test.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.test.com")
    monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.test.com")
    return email_adapter.EmailAdapter(PlatformConfig(enabled=True, extra=extra or {}))


def _capture_adapter_message(monkeypatch, adapter):
    smtp = MagicMock()
    monkeypatch.setattr(adapter, "_connect_smtp", lambda: smtp)
    monkeypatch.setattr(email_adapter, "formatdate", lambda *, localtime: _DATE)
    monkeypatch.setattr(
        email_adapter.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="0123456789abcdef"),
    )
    return smtp


def _assert_utf8_plain_part(part, expected_body: str) -> None:
    assert part.get_content_type() == "text/plain"
    assert part.get_content_charset() == "utf-8"
    assert part["Content-Transfer-Encoding"] == "base64"
    assert part.get_payload(decode=True).decode("utf-8") == expected_body


def _build_message_with_attachment(filename: str):
    return build_email_message(
        from_address="hermes@test.com",
        to_address="user@test.com",
        subject="Attachment",
        body="Attached.",
        date=_DATE,
        attachments=(MimeAttachment(filename=filename, content=b"payload"),),
    )


def _build_rich_message(
    *,
    html_body: str,
    inline_images=(),
    attachments=(),
):
    return build_email_message(
        from_address="hermes@test.com",
        to_address="user@test.com",
        subject="CID message",
        body="Canonical plain text.",
        date=_DATE,
        message_id=_MESSAGE_ID,
        in_reply_to="<original@test.com>",
        references="<original@test.com>",
        html_body=html_body,
        inline_images=inline_images,
        attachments=attachments,
    )


def _parse_message(message):
    return BytesParser(policy=policy.default).parsebytes(
        message.as_bytes(policy=policy.SMTP)
    )

def test_plain_reply_preserves_legacy_multipart_envelope_and_threading(monkeypatch):
    adapter = _make_adapter(monkeypatch)
    smtp = _capture_adapter_message(monkeypatch, adapter)
    adapter._thread_context["user@test.com"] = {
        "subject": "Résumé du projet",
        "message_id": "<context@test.com>",
    }

    returned_id = adapter._send_email(
        "user@test.com",
        "Réponse en texte brut.",
        "<explicit@test.com>",
    )

    message = smtp.send_message.call_args.args[0]
    assert returned_id == _MESSAGE_ID
    assert message.get_content_type() == "multipart/mixed"
    assert list(message.keys()) == [
        "Content-Type",
        "MIME-Version",
        "From",
        "To",
        "Subject",
        "In-Reply-To",
        "References",
        "Date",
        "Message-ID",
    ]
    assert message["From"] == "hermes@test.com"
    assert message["To"] == "user@test.com"
    assert message["Subject"] == "Re: Résumé du projet"
    assert message["In-Reply-To"] == "<explicit@test.com>"
    assert message["References"] == "<explicit@test.com>"
    assert message["Date"] == _DATE
    assert message["Message-ID"] == _MESSAGE_ID
    assert [part.get_content_type() for part in message.walk()] == [
        "multipart/mixed",
        "text/plain",
    ]
    _assert_utf8_plain_part(message.get_payload()[0], "Réponse en texte brut.")
    smtp.login.assert_called_once_with("hermes@test.com", "secret")
    smtp.quit.assert_called_once_with()


def test_single_attachment_preserves_legacy_shape_headers_and_payload(
    monkeypatch,
    tmp_path: Path,
):
    adapter = _make_adapter(monkeypatch)
    smtp = _capture_adapter_message(monkeypatch, adapter)
    adapter._thread_context["user@test.com"] = {
        "subject": "Re: Existing subject",
        "message_id": "<original@test.com>",
    }
    source = tmp_path / "source.bin"
    source.write_bytes(b"\x00attachment\xff")

    returned_id = adapter._send_email_with_attachment(
        "user@test.com",
        "Pièce jointe.",
        str(source),
        "rapport final.bin",
    )

    message = smtp.send_message.call_args.args[0]
    assert returned_id == _MESSAGE_ID
    assert message.get_content_type() == "multipart/mixed"
    assert list(message.keys()) == [
        "Content-Type",
        "MIME-Version",
        "From",
        "To",
        "Subject",
        "In-Reply-To",
        "References",
        "Date",
        "Message-ID",
    ]
    assert message["Subject"] == "Re: Existing subject"
    assert message["In-Reply-To"] == "<original@test.com>"
    assert message["References"] == "<original@test.com>"
    assert [part.get_content_type() for part in message.walk()] == [
        "multipart/mixed",
        "text/plain",
        "application/octet-stream",
    ]
    plain_part, attachment = message.get_payload()
    _assert_utf8_plain_part(plain_part, "Pièce jointe.")
    assert attachment["Content-Transfer-Encoding"] == "base64"
    assert attachment["Content-Disposition"] == (
        'attachment; filename="rapport final.bin"'
    )
    assert attachment.get_filename() == "rapport final.bin"
    assert attachment.get_payload(decode=True) == b"\x00attachment\xff"


@pytest.mark.parametrize(
    "filename",
    [
        "report.txt",
        "report final.txt",
        'report "final".txt',
        "report;final.txt",
        "résumé-été.txt",
    ],
)
def test_attachment_filename_round_trips_through_serialization(filename):
    message = _build_message_with_attachment(filename)

    serialized = message.as_bytes(policy=policy.SMTP)
    parsed = BytesParser(policy=policy.default).parsebytes(serialized)
    attachment = next(parsed.iter_attachments())

    assert attachment.get_filename() == filename
    assert parsed["X-Injected"] is None


def test_attachment_filename_uses_quoted_and_rfc2231_parameters():
    spaced = _build_message_with_attachment("report final.txt").as_bytes(
        policy=policy.SMTP
    )
    punctuation = _build_message_with_attachment(
        'report; "final".txt'
    ).as_bytes(policy=policy.SMTP)
    unicode = _build_message_with_attachment("résumé-été.txt").as_bytes(
        policy=policy.SMTP
    )

    assert b'filename="report final.txt"' in spaced
    assert b'filename="report; \\"final\\".txt"' in punctuation
    assert b"filename*=utf-8''r%C3%A9sum%C3%A9-%C3%A9t%C3%A9.txt" in unicode


@pytest.mark.parametrize(
    "filename",
    [
        "report\rinjected.txt",
        "report\ninjected.txt",
        "report.txt\r\nX-Injected: true",
    ],
    ids=["cr", "lf", "crlf-header"],
)
def test_attachment_filename_rejects_header_newlines(filename):
    with pytest.raises(
        ValueError,
        match="attachment filename must not contain NUL, CR, or LF characters",
    ):
        _build_message_with_attachment(filename)


def test_attachment_filename_rejects_nul():
    with pytest.raises(
        ValueError,
        match="attachment filename must not contain NUL, CR, or LF characters",
    ):
        _build_message_with_attachment("report\x00final.txt")


def test_multiple_attachments_preserve_order_and_empty_body_semantics(
    monkeypatch,
    tmp_path: Path,
):
    adapter = _make_adapter(monkeypatch)
    smtp = _capture_adapter_message(monkeypatch, adapter)
    first = tmp_path / "first.dat"
    second = tmp_path / "second.dat"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    adapter._send_email_with_attachments(
        "user@test.com",
        "",
        [str(first), str(second)],
    )

    message = smtp.send_message.call_args.args[0]
    assert message.get_content_type() == "multipart/mixed"
    assert list(message.keys()) == [
        "Content-Type",
        "MIME-Version",
        "From",
        "To",
        "Subject",
        "Date",
        "Message-ID",
    ]
    assert message["Subject"] == "Re: Hermes Agent"
    assert message["In-Reply-To"] is None
    assert message["References"] is None
    assert [part.get_content_type() for part in message.walk()] == [
        "multipart/mixed",
        "application/octet-stream",
        "application/octet-stream",
    ]
    attachments = message.get_payload()
    assert [part.get_filename() for part in attachments] == [
        "first.dat",
        "second.dat",
    ]
    assert [part.get_payload(decode=True) for part in attachments] == [
        b"first",
        b"second",
    ]


def test_html_with_attachments_and_omitted_empty_body_remains_attachments_only():
    message = build_email_message(
        from_address="hermes@test.com",
        to_address="user@test.com",
        subject="Attachment only",
        body="",
        date=_DATE,
        html_body="<p>Rendered empty body</p>",
        attachments=(MimeAttachment(filename="report.bin", content=b"report"),),
        include_empty_body=False,
    )

    assert message.get_content_type() == "multipart/mixed"
    assert [part.get_content_type() for part in message.walk()] == [
        "multipart/mixed",
        "application/octet-stream",
    ]
    attachment = message.get_payload()[0]
    assert attachment.get_filename() == "report.bin"
    assert attachment.get_payload(decode=True) == b"report"


def test_standalone_send_preserves_legacy_text_plain_envelope(monkeypatch):
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    monkeypatch.setenv("EMAIL_SMTP_PORT", "587")
    smtp = MagicMock()
    monkeypatch.setattr(email_adapter.smtplib, "SMTP", MagicMock(return_value=smtp))
    monkeypatch.setattr(email_adapter, "formatdate", lambda *, localtime: _DATE)
    config = SimpleNamespace(
        token=None,
        api_key=None,
        extra={"address": "hermes@test.com", "smtp_host": "smtp.test.com"},
    )

    result = asyncio.run(
        email_adapter._standalone_send(
            config,
            "user@test.com",
            "Message autonome accentué.",
        )
    )

    message = smtp.send_message.call_args.args[0]
    assert result == {
        "success": True,
        "platform": "email",
        "chat_id": "user@test.com",
    }
    assert message.get_content_type() == "text/plain"
    assert not message.is_multipart()
    assert list(message.keys()) == [
        "Content-Type",
        "MIME-Version",
        "Content-Transfer-Encoding",
        "From",
        "To",
        "Subject",
        "Date",
    ]
    assert message["From"] == "hermes@test.com"
    assert message["To"] == "user@test.com"
    assert message["Subject"] == "Hermes Agent"
    assert message["Date"] == _DATE
    assert message["Message-ID"] is None
    assert message["In-Reply-To"] is None
    assert message["References"] is None
    _assert_utf8_plain_part(message, "Message autonome accentué.")


def test_rich_html_reply_uses_sanitized_markdown_alternative(monkeypatch):
    adapter = _make_adapter(monkeypatch, {"rich_html_enabled": True})
    smtp = _capture_adapter_message(monkeypatch, adapter)
    adapter._thread_context["user@test.com"] = {
        "subject": "Formatting",
        "message_id": "<original@test.com>",
    }
    markdown_body = "# Résumé\n\nTexte **important** avec [lien](https://example.com)."

    adapter._send_email("user@test.com", markdown_body)

    message = smtp.send_message.call_args.args[0]
    assert message.get_content_type() == "multipart/alternative"
    assert message["Subject"] == "Re: Formatting"
    assert message["In-Reply-To"] == "<original@test.com>"
    assert message["References"] == "<original@test.com>"
    assert [part.get_content_type() for part in message.walk()] == [
        "multipart/alternative",
        "text/plain",
        "text/html",
    ]
    plain_part, html_part = message.get_payload()
    _assert_utf8_plain_part(plain_part, markdown_body)
    assert html_part.get_content_charset() == "utf-8"
    rendered = html_part.get_payload(decode=True).decode("utf-8")
    assert "<h1>Résumé</h1>" in rendered
    assert "<strong>important</strong>" in rendered
    assert 'href="https://example.com"' in rendered


def test_rich_html_with_attachment_nests_alternative_before_attachment(
    monkeypatch,
    tmp_path: Path,
):
    adapter = _make_adapter(monkeypatch, {"rich_html_enabled": "true"})
    smtp = _capture_adapter_message(monkeypatch, adapter)
    source = tmp_path / "report.bin"
    source.write_bytes(b"report")

    adapter._send_email_with_attachment(
        "user@test.com",
        "Voir **rapport**.",
        str(source),
    )

    message = smtp.send_message.call_args.args[0]
    assert message.get_content_type() == "multipart/mixed"
    assert [part.get_content_type() for part in message.walk()] == [
        "multipart/mixed",
        "multipart/alternative",
        "text/plain",
        "text/html",
        "application/octet-stream",
    ]
    alternative, attachment = message.get_payload()
    assert [part.get_content_type() for part in alternative.get_payload()] == [
        "text/plain",
        "text/html",
    ]
    assert attachment.get_filename() == "report.bin"
    assert attachment.get_payload(decode=True) == b"report"


def test_rich_html_with_cid_uses_related_around_alternative_and_round_trips():
    image = MimeInlineImage(
        filename="signature-logo.png",
        content=b"png-payload",
        content_type="image/png",
    )
    html_body = sanitize_message_html(
        f'<p>Hello.</p><img src="cid:{image.content_id}" alt="Logo">',
        allowed_cids=(image.content_id,),
    )

    parsed = _parse_message(
        _build_rich_message(html_body=html_body, inline_images=(image,))
    )

    assert [part.get_content_type() for part in parsed.walk()] == [
        "multipart/related",
        "multipart/alternative",
        "text/plain",
        "text/html",
        "image/png",
    ]
    alternative, inline_part = parsed.get_payload()
    assert parsed.get_param("type") == "multipart/alternative"
    assert alternative.get_content_type() == "multipart/alternative"
    plain_part, html_part = alternative.get_payload()
    _assert_utf8_plain_part(plain_part, "Canonical plain text.")
    rendered = html_part.get_content()
    assert rendered.count(f"cid:{image.content_id}") == 1
    assert inline_part["Content-ID"] == f"<{image.content_id}>"
    assert inline_part.get_content_disposition() == "inline"
    assert inline_part.get_filename() == "signature-logo.png"
    assert inline_part.get_payload(decode=True) == b"png-payload"
    assert parsed["Message-ID"] == _MESSAGE_ID
    assert parsed["In-Reply-To"] == "<original@test.com>"
    assert parsed["References"] == "<original@test.com>"


def test_rich_html_with_multiple_cids_attaches_each_unique_image_once():
    first = MimeInlineImage(
        filename="first.png",
        content=b"first",
        content_type="image/png",
    )
    second = MimeInlineImage(
        filename="second.jpg",
        content=b"second",
        content_type="image/jpeg",
    )
    html_body = sanitize_message_html(
        (
            f'<img src="cid:{first.content_id}" alt="First">'
            f'<img src="cid:{second.content_id}" alt="Second">'
        ),
        allowed_cids=(first.content_id, second.content_id),
    )

    parsed = _parse_message(
        _build_rich_message(
            html_body=html_body,
            inline_images=(first, second),
        )
    )

    assert [part.get_content_type() for part in parsed.walk()] == [
        "multipart/related",
        "multipart/alternative",
        "text/plain",
        "text/html",
        "image/png",
        "image/jpeg",
    ]
    content_ids = [part["Content-ID"] for part in parsed.get_payload()[1:]]
    assert content_ids == [f"<{first.content_id}>", f"<{second.content_id}>"]
    assert len(content_ids) == len(set(content_ids)) == 2


def test_cid_with_regular_attachment_uses_mixed_related_alternative_tree():
    image = MimeInlineImage(
        filename="signature-logo.webp",
        content=b"webp-payload",
        content_type="image/webp",
    )
    html_body = sanitize_message_html(
        f'<p>Report.</p><img src="cid:{image.content_id}" alt="Logo">',
        allowed_cids=(image.content_id,),
    )

    parsed = _parse_message(
        _build_rich_message(
            html_body=html_body,
            inline_images=(image,),
            attachments=(
                MimeAttachment(filename="report final.bin", content=b"report"),
            ),
        )
    )

    assert [part.get_content_type() for part in parsed.walk()] == [
        "multipart/mixed",
        "multipart/related",
        "multipart/alternative",
        "text/plain",
        "text/html",
        "image/webp",
        "application/octet-stream",
    ]
    related, attachment = parsed.get_payload()
    assert related.get_param("type") == "multipart/alternative"
    alternative, inline_part = related.get_payload()
    assert alternative.get_content_type() == "multipart/alternative"
    assert inline_part["Content-ID"] == f"<{image.content_id}>"
    assert attachment.get_content_disposition() == "attachment"
    assert attachment.get_filename() == "report final.bin"
    assert attachment.get_payload(decode=True) == b"report"


def test_repeated_cid_reference_attaches_the_inline_image_only_once():
    image = MimeInlineImage(
        filename="signature-logo.gif",
        content=b"gif-payload",
        content_type="image/gif",
    )
    html_body = sanitize_message_html(
        (
            f'<img src="cid:{image.content_id}" alt="First use">'
            f'<img src="cid:{image.content_id}" alt="Second use">'
        ),
        allowed_cids=(image.content_id,),
    )

    parsed = _parse_message(
        _build_rich_message(html_body=html_body, inline_images=(image,))
    )

    assert parsed.get_body(preferencelist=("html",)).get_content().count(
        f"cid:{image.content_id}"
    ) == 2
    assert len(
        [part for part in parsed.walk() if part.get_content_disposition() == "inline"]
    ) == 1


def test_unreferenced_inline_image_is_not_added_and_html_shape_is_unchanged():
    image = MimeInlineImage(
        filename="unused.png",
        content=b"unused",
        content_type="image/png",
    )
    html_body = sanitize_message_html("<p>No inline image.</p>")

    parsed = _parse_message(
        _build_rich_message(html_body=html_body, inline_images=(image,))
    )

    assert [part.get_content_type() for part in parsed.walk()] == [
        "multipart/alternative",
        "text/plain",
        "text/html",
    ]
    assert parsed.get_body(preferencelist=("html",)).get_content() == html_body


def test_message_sanitizer_allows_only_explicit_backend_cids():
    image = MimeInlineImage(
        filename="allowed.png",
        content=b"allowed",
        content_type="image/png",
    )
    cleaned = sanitize_message_html(
        (
            f'<img src="cid:{image.content_id}" alt="Allowed">'
            '<img src="cid:user-controlled@example.test" alt="Rejected">'
            '<img src="https://tracker.example/pixel.png" alt="Remote">'
        ),
        allowed_cids=(image.content_id,),
    )

    assert f'src="cid:{image.content_id}"' in cleaned
    assert "cid:user-controlled@example.test" not in cleaned
    assert "https://tracker.example" not in cleaned


def test_signature_sanitizer_uses_its_own_exact_cid_allowlist():
    image = MimeInlineImage(
        filename="signature-logo.png",
        content=b"logo",
        content_type="image/png",
    )
    cleaned = sanitize_signature_html(
        (
            f'<div><img src="cid:{image.content_id}" alt="Logo" '
            'style="width: 120px; position: fixed"></div>'
            '<img src="cid:not-allowed@example.test" alt="Rejected">'
            '<img src="https://tracker.example/pixel.png" alt="Remote">'
        ),
        allowed_cids=(image.content_id,),
    )

    assert f'src="cid:{image.content_id}"' in cleaned
    assert "width:120px" in cleaned
    assert "position:" not in cleaned
    assert "cid:not-allowed@example.test" not in cleaned
    assert "https://tracker.example" not in cleaned


def test_signature_sanitizer_keeps_only_generated_logo_layout_attributes():
    image = MimeInlineImage(
        filename="signature-logo.png",
        content=b"logo",
        content_type="image/png",
    )
    cleaned = sanitize_signature_html(
        (
            f'<img src="cid:{image.content_id}" alt="Signature logo" '
            'width="230" height="999" title="user supplied" '
            'style="display:block; width:230px; max-width:100%; '
            'height:auto; border:0; position:fixed;">'
        ),
        allowed_cids=(image.content_id,),
    )

    assert f'src="cid:{image.content_id}"' in cleaned
    assert 'alt="Signature logo"' in cleaned
    assert 'width="230"' in cleaned
    assert 'height="999"' not in cleaned
    assert 'title="user supplied"' not in cleaned
    assert 'style="display:block;width:230px;max-width:100%;height:auto;border:0"' in cleaned
    assert "position:" not in cleaned


def test_inline_content_id_is_generated_and_cannot_be_supplied_by_caller():
    first = MimeInlineImage(
        filename="first.png",
        content=b"first",
        content_type="image/png",
    )
    second = MimeInlineImage(
        filename="second.png",
        content=b"second",
        content_type="image/png",
    )

    assert first.content_id != second.content_id
    assert first.content_id.endswith("@inline.invalid")
    assert not any(character in first.content_id for character in "<>\x00\r\n")
    with pytest.raises(TypeError):
        MimeInlineImage(
            filename="attacker.png",
            content=b"attacker",
            content_type="image/png",
            content_id="user-controlled@example.test",
        )


@pytest.mark.parametrize("filename", ["logo\x00.png", "logo\r.png", "logo\n.png"])
def test_inline_image_filename_preserves_control_character_hardening(filename):
    image = MimeInlineImage(
        filename=filename,
        content=b"logo",
        content_type="image/png",
    )
    html_body = sanitize_message_html(
        f'<img src="cid:{image.content_id}" alt="Logo">',
        allowed_cids=(image.content_id,),
    )

    with pytest.raises(
        ValueError,
        match="attachment filename must not contain NUL, CR, or LF characters",
    ):
        _build_rich_message(html_body=html_body, inline_images=(image,))


def test_standalone_rich_html_uses_markdown_alternative(monkeypatch):
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    monkeypatch.setenv("EMAIL_SMTP_PORT", "587")
    smtp = MagicMock()
    monkeypatch.setattr(email_adapter.smtplib, "SMTP", MagicMock(return_value=smtp))
    monkeypatch.setattr(email_utils, "formatdate", lambda *, localtime: _DATE)
    config = SimpleNamespace(
        token=None,
        api_key=None,
        extra={
            "address": "hermes@test.com",
            "smtp_host": "smtp.test.com",
            "rich_html_enabled": True,
        },
    )

    result = asyncio.run(
        email_adapter._standalone_send(
            config,
            "user@test.com",
            "Message **riche**.",
        )
    )

    assert result["success"] is True
    message = smtp.send_message.call_args.args[0]
    assert message.get_content_type() == "multipart/alternative"
    assert [part.get_content_type() for part in message.walk()] == [
        "multipart/alternative",
        "text/plain",
        "text/html",
    ]
    plain_part, html_part = message.get_payload()
    _assert_utf8_plain_part(plain_part, "Message **riche**.")
    rendered = html_part.get_payload(decode=True).decode("utf-8")
    assert "<strong>riche</strong>" in rendered


def test_explicitly_disabled_rich_html_keeps_legacy_mime_shape(monkeypatch):
    adapter = _make_adapter(monkeypatch, {"rich_html_enabled": False})
    smtp = _capture_adapter_message(monkeypatch, adapter)
    renderer = MagicMock(side_effect=AssertionError("renderer must stay disabled"))
    monkeypatch.setattr(email_adapter, "render_markdown_html", renderer)

    adapter._send_email("user@test.com", "Texte **non rendu**.")

    message = smtp.send_message.call_args.args[0]
    assert message.get_content_type() == "multipart/mixed"
    assert [part.get_content_type() for part in message.walk()] == [
        "multipart/mixed",
        "text/plain",
    ]
    _assert_utf8_plain_part(message.get_payload()[0], "Texte **non rendu**.")
    renderer.assert_not_called()


def test_markdown_rendering_sanitizes_dangerous_raw_html_and_urls():
    rendered = render_markdown_html(
        """# Safe heading

<script>alert('script')</script>
<p onclick="alert('event')">Paragraph</p>
[unsafe](javascript:alert('link'))
<a href="javascript:alert('raw')" onmouseover="alert('hover')">raw link</a>
"""
    )

    assert "<h1>Safe heading</h1>" in rendered
    assert "Paragraph" in rendered
    assert "unsafe" in rendered
    assert "raw link" in rendered
    lowered = rendered.lower()
    assert "<script" not in lowered
    assert "onclick" not in lowered
    assert "onmouseover" not in lowered
    assert "javascript:" not in lowered
    assert "script')" not in lowered


def test_message_sanitizer_preserves_safe_links_and_drops_active_content():
    cleaned = sanitize_message_html(
        '<p class="discarded">Safe <a href="https://example.com">link</a></p>'
        '<img src="https://tracker.example/pixel.png" onerror="alert(1)">'
        "<style>body { display: none }</style>"
    )

    assert cleaned.startswith("<p>Safe ")
    assert 'href="https://example.com"' in cleaned
    assert 'rel="noopener noreferrer"' in cleaned
    lowered = cleaned.lower()
    assert "class=" not in lowered
    assert "<img" not in lowered
    assert "onerror" not in lowered
    assert "<style" not in lowered
    assert "display: none" not in lowered


def test_message_sanitizer_drops_relative_urls():
    cleaned = sanitize_message_html(
        '<p><a href="/docs/setup">relative link</a> '
        '<a href="https://example.com/docs/setup">absolute link</a></p>'
    )

    assert "relative link" in cleaned
    assert 'href="/docs/setup"' not in cleaned
    assert 'href="https://example.com/docs/setup"' in cleaned


@pytest.mark.parametrize(
    "signature",
    [
        "John",
        {"enabled": True, "text": ""},
    ],
    ids=["not-a-mapping", "enabled-with-empty-text"],
)
def test_invalid_signature_config_disables_only_signature_and_keeps_rich_html(
    monkeypatch,
    caplog,
    signature,
):
    adapter = _make_adapter(
        monkeypatch,
        {"rich_html_enabled": True, "signature": signature},
    )
    smtp = _capture_adapter_message(monkeypatch, adapter)

    adapter._send_email("user@test.com", "Message **riche**.")

    assert adapter._signature is None
    assert adapter._rich_html_enabled is True
    message = smtp.send_message.call_args.args[0]
    assert message.get_content_type() == "multipart/alternative"
    plain_part, html_part = message.get_payload()
    _assert_utf8_plain_part(plain_part, "Message **riche**.")
    assert "<strong>riche</strong>" in html_part.get_payload(decode=True).decode(
        "utf-8"
    )
    assert "Invalid Email signature configuration; signature disabled" in caplog.text


def test_valid_signature_config_initializes_unchanged(monkeypatch):
    adapter = _make_adapter(
        monkeypatch,
        {
            "rich_html_enabled": True,
            "signature": {
                "enabled": True,
                "text": "Canonical signature",
                "html": "<strong>Rendered signature</strong>",
            },
        },
    )

    assert adapter._signature == MimeSignature(
        text="Canonical signature",
        html="<strong>Rendered signature</strong>",
    )


def test_disabled_signature_keeps_unsigned_legacy_body(monkeypatch):
    adapter = _make_adapter(
        monkeypatch,
        {
            "signature": {
                "enabled": False,
                "text": "Must not appear",
                "html": "<strong>Must not appear</strong>",
            }
        },
    )
    smtp = _capture_adapter_message(monkeypatch, adapter)

    adapter._send_email("user@test.com", "Original body")

    message = smtp.send_message.call_args.args[0]
    assert message.get_content_type() == "multipart/mixed"
    _assert_utf8_plain_part(message.get_payload()[0], "Original body")
    assert "Must not appear" not in message.as_string()


def test_plain_signature_appends_canonical_text_without_changing_mime_shape(
    monkeypatch,
):
    adapter = _make_adapter(
        monkeypatch,
        {
            "signature": {
                "enabled": True,
                "text": "Hermes Agent\nInternal assistant",
            }
        },
    )
    smtp = _capture_adapter_message(monkeypatch, adapter)

    adapter._send_email("user@test.com", "Original body")

    message = smtp.send_message.call_args.args[0]
    assert message.get_content_type() == "multipart/mixed"
    assert [part.get_content_type() for part in message.walk()] == [
        "multipart/mixed",
        "text/plain",
    ]
    _assert_utf8_plain_part(
        message.get_payload()[0],
        "Original body\n\nHermes Agent\nInternal assistant",
    )


def test_rich_signature_derives_html_from_canonical_text(monkeypatch):
    signature_text = "**Hermes Agent**\n\nInternal assistant"
    adapter = _make_adapter(
        monkeypatch,
        {
            "rich_html_enabled": True,
            "signature": {"enabled": True, "text": signature_text},
        },
    )
    smtp = _capture_adapter_message(monkeypatch, adapter)

    adapter._send_email("user@test.com", "Hello **there**.")

    message = smtp.send_message.call_args.args[0]
    plain_part, html_part = message.get_payload()
    plain = plain_part.get_payload(decode=True).decode("utf-8")
    rendered = html_part.get_payload(decode=True).decode("utf-8")
    assert plain == f"Hello **there**.\n\n{signature_text}"
    assert plain.count("Hermes Agent") == 1
    assert rendered.count("Hermes Agent") == 1
    assert "<strong>Hermes Agent</strong>" in rendered
    assert "<p>Internal assistant</p>" in rendered


def test_provided_signature_html_uses_separate_sanitizer_policy(monkeypatch):
    signature_html = (
        '<div style="color: #663399; position: fixed" onclick="alert(1)">'
        "<strong>Signature Team</strong>"
        "<script>alert('script')</script>"
        '<a href="javascript:alert(2)">unsafe</a>'
        '<a href="mailto:team@example.com">mail</a>'
        '<img src="https://tracker.example/pixel.png">'
        "</div>"
    )
    adapter = _make_adapter(
        monkeypatch,
        {
            "rich_html_enabled": True,
            "signature": {
                "enabled": True,
                "text": "Canonical signature",
                "html": signature_html,
            },
        },
    )
    smtp = _capture_adapter_message(monkeypatch, adapter)

    adapter._send_email("user@test.com", "Message body")

    plain_part, html_part = smtp.send_message.call_args.args[0].get_payload()
    plain = plain_part.get_payload(decode=True).decode("utf-8")
    rendered = html_part.get_payload(decode=True).decode("utf-8")
    assert plain == "Message body\n\nCanonical signature"
    assert plain.count("Canonical signature") == 1
    assert rendered.count("Signature Team") == 1
    assert "<div" in rendered
    assert "color:" in rendered
    assert 'href="mailto:team@example.com"' in rendered
    lowered = rendered.lower()
    assert "position:" not in lowered
    assert "onclick" not in lowered
    assert "<script" not in lowered
    assert "javascript:" not in lowered
    assert "<img" not in lowered


def test_signature_sanitizer_keeps_safe_signature_layout_only():
    cleaned = sanitize_signature_html(
        '<div style="font-weight: bold; position: fixed">'
        '<span style="color: blue; background-image: url(https://tracker)">Team</span>'
        '<a href="tel:+33123456789">Call</a>'
        "</div>"
    )

    assert "<div" in cleaned
    assert "<span" in cleaned
    assert "font-weight:bold" in cleaned
    assert "color:blue" in cleaned
    assert 'href="tel:+33123456789"' in cleaned
    lowered = cleaned.lower()
    assert "position:" not in lowered
    assert "background-image" not in lowered


def test_signature_adds_plain_part_to_empty_attachment_message(
    monkeypatch,
    tmp_path: Path,
):
    adapter = _make_adapter(
        monkeypatch,
        {"signature": {"enabled": True, "text": "Attachment signature"}},
    )
    smtp = _capture_adapter_message(monkeypatch, adapter)
    source = tmp_path / "report.bin"
    source.write_bytes(b"report")

    adapter._send_email_with_attachment("user@test.com", "", str(source))

    message = smtp.send_message.call_args.args[0]
    assert [part.get_content_type() for part in message.walk()] == [
        "multipart/mixed",
        "text/plain",
        "application/octet-stream",
    ]
    plain_part, attachment = message.get_payload()
    _assert_utf8_plain_part(plain_part, "Attachment signature")
    assert attachment.get_payload(decode=True) == b"report"


def test_standalone_rich_signature_is_added_once_to_both_alternatives(monkeypatch):
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    monkeypatch.setenv("EMAIL_SMTP_PORT", "587")
    smtp = MagicMock()
    monkeypatch.setattr(email_adapter.smtplib, "SMTP", MagicMock(return_value=smtp))
    monkeypatch.setattr(email_utils, "formatdate", lambda *, localtime: _DATE)
    config = SimpleNamespace(
        token=None,
        api_key=None,
        extra={
            "address": "hermes@test.com",
            "smtp_host": "smtp.test.com",
            "rich_html_enabled": True,
            "signature": {"enabled": True, "text": "Standalone signature"},
        },
    )

    result = asyncio.run(
        email_adapter._standalone_send(config, "user@test.com", "Standalone body")
    )

    assert result["success"] is True
    plain_part, html_part = smtp.send_message.call_args.args[0].get_payload()
    plain = plain_part.get_payload(decode=True).decode("utf-8")
    rendered = html_part.get_payload(decode=True).decode("utf-8")
    assert plain == "Standalone body\n\nStandalone signature"
    assert plain.count("Standalone signature") == 1
    assert rendered.count("Standalone signature") == 1


def test_signature_logo_token_without_asset_is_removed_and_mail_is_sent(
    monkeypatch,
):
    adapter = _make_adapter(
        monkeypatch,
        {
            "rich_html_enabled": True,
            "signature": {
                "enabled": True,
                "text": "Canonical signature",
                "html": "<div>Team {{email_signature_logo}}</div>",
            },
        },
    )
    smtp = _capture_adapter_message(monkeypatch, adapter)
    loader = MagicMock(return_value=None)
    monkeypatch.setattr(email_adapter, "load_signature_logo_inline_image", loader)

    adapter._send_email("user@test.com", "Message body")

    message = smtp.send_message.call_args.args[0]
    assert message.get_content_type() == "multipart/alternative"
    assert [part.get_content_type() for part in message.walk()] == [
        "multipart/alternative",
        "text/plain",
        "text/html",
    ]
    plain_part, html_part = message.get_payload()
    _assert_utf8_plain_part(
        plain_part,
        "Message body\n\nCanonical signature",
    )
    rendered = html_part.get_payload(decode=True).decode("utf-8")
    assert "{{email_signature_logo}}" not in rendered
    assert "cid:" not in rendered
    loader.assert_called_once_with(
        signature_enabled=True,
        rich_html_enabled=True,
    )


def test_valid_signature_logo_token_generates_referenced_cid_and_threading(
    monkeypatch,
):
    adapter = _make_adapter(
        monkeypatch,
        {
            "rich_html_enabled": True,
            "signature": {
                "enabled": True,
                "text": "Canonical signature",
                "html": "<div>Team {{email_signature_logo}}</div>",
            },
        },
    )
    adapter._thread_context["user@test.com"] = {
        "subject": "Logo thread",
        "message_id": "<original@test.com>",
    }
    smtp = _capture_adapter_message(monkeypatch, adapter)
    logo = MimeInlineImage(
        filename="signature-logo.png",
        content=b"logo",
        content_type="image/png",
    )
    monkeypatch.setattr(
        email_adapter,
        "load_signature_logo_inline_image",
        MagicMock(return_value=logo),
    )

    adapter._send_email("user@test.com", "Message body")

    parsed = _parse_message(smtp.send_message.call_args.args[0])
    assert [part.get_content_type() for part in parsed.walk()] == [
        "multipart/related",
        "multipart/alternative",
        "text/plain",
        "text/html",
        "image/png",
    ]
    alternative, inline_part = parsed.get_payload()
    plain_part, html_part = alternative.get_payload()
    _assert_utf8_plain_part(
        plain_part,
        "Message body\n\nCanonical signature",
    )
    rendered = html_part.get_content()
    assert rendered.count(f"cid:{logo.content_id}") == 1
    assert '<img src="cid:' in rendered
    assert 'alt="Signature logo"' in rendered
    assert "{{email_signature_logo}}" not in rendered
    assert inline_part["Content-ID"] == f"<{logo.content_id}>"
    assert inline_part.get_payload(decode=True) == b"logo"
    assert parsed["In-Reply-To"] == "<original@test.com>"
    assert parsed["References"] == "<original@test.com>"


def test_signature_logo_uses_default_width_and_exact_generated_tag(monkeypatch):
    adapter = _make_adapter(
        monkeypatch,
        {
            "rich_html_enabled": True,
            "signature": {
                "enabled": True,
                "text": "Canonical signature",
                "html": "<div>{{email_signature_logo}}</div>",
            },
        },
    )
    smtp = _capture_adapter_message(monkeypatch, adapter)
    logo = MimeInlineImage(
        filename="signature-logo.png",
        content=b"logo",
        content_type="image/png",
    )
    monkeypatch.setattr(
        email_adapter,
        "load_signature_logo_inline_image",
        MagicMock(return_value=logo),
    )

    adapter._send_email("user@test.com", "Message body")

    parsed = _parse_message(smtp.send_message.call_args.args[0])
    rendered = parsed.get_body(preferencelist=("html",)).get_content()
    expected_tag = (
        f'<img src="cid:{logo.content_id}" alt="Signature logo" width="230" '
        'style="display:block;width:230px;max-width:100%;height:auto;border:0">'
    )
    assert expected_tag in rendered
    assert [part.get_content_type() for part in parsed.walk()] == [
        "multipart/related",
        "multipart/alternative",
        "text/plain",
        "text/html",
        "image/png",
    ]


@pytest.mark.parametrize("logo_width", [32, 480, 1024])
def test_signature_logo_accepts_custom_width_with_inclusive_bounds(
    monkeypatch,
    logo_width,
):
    adapter = _make_adapter(
        monkeypatch,
        {
            "rich_html_enabled": True,
            "signature": {
                "enabled": True,
                "text": "Canonical signature",
                "html": "<div>{{email_signature_logo}}</div>",
                "logo_width": logo_width,
            },
        },
    )
    smtp = _capture_adapter_message(monkeypatch, adapter)
    logo = MimeInlineImage(
        filename="signature-logo.png",
        content=b"logo",
        content_type="image/png",
    )
    monkeypatch.setattr(
        email_adapter,
        "load_signature_logo_inline_image",
        MagicMock(return_value=logo),
    )

    adapter._send_email("user@test.com", "Message body")

    parsed = _parse_message(smtp.send_message.call_args.args[0])
    rendered = parsed.get_body(preferencelist=("html",)).get_content()
    _assert_utf8_plain_part(
        parsed.get_body(preferencelist=("plain",)),
        "Message body\n\nCanonical signature",
    )
    assert f'width="{logo_width}"' in rendered
    assert f"width:{logo_width}px" in rendered
    assert "height:auto" in rendered
    assert "height=" not in rendered


@pytest.mark.parametrize(
    "logo_width",
    [0, 31, 1025, -1, 230.5, "230", True, None],
)
def test_signature_logo_rejects_invalid_width(monkeypatch, logo_width):
    with pytest.raises(
        ValueError,
        match=r"signature\.logo_width.*integer between 32 and 1024",
    ):
        _make_adapter(
            monkeypatch,
            {
                "signature": {
                    "enabled": True,
                    "text": "Canonical signature",
                    "logo_width": logo_width,
                }
            },
        )


def test_multiple_signature_logo_tokens_share_one_inline_mime_part(monkeypatch):
    adapter = _make_adapter(
        monkeypatch,
        {
            "rich_html_enabled": True,
            "signature": {
                "enabled": True,
                "text": "Canonical signature",
                "html": (
                    "<div>{{email_signature_logo}}"
                    "<span>Team</span>{{email_signature_logo}}</div>"
                ),
                "logo_width": 360,
            },
        },
    )
    smtp = _capture_adapter_message(monkeypatch, adapter)
    logo = MimeInlineImage(
        filename="signature-logo.gif",
        content=b"logo",
        content_type="image/gif",
    )
    monkeypatch.setattr(
        email_adapter,
        "load_signature_logo_inline_image",
        MagicMock(return_value=logo),
    )

    adapter._send_email("user@test.com", "Message body")

    parsed = _parse_message(smtp.send_message.call_args.args[0])
    rendered = parsed.get_body(preferencelist=("html",)).get_content()
    assert rendered.count(f"cid:{logo.content_id}") == 2
    assert rendered.count('width="360"') == 2
    assert rendered.count("width:360px") == 2
    assert len(
        [part for part in parsed.walk() if part.get_content_disposition() == "inline"]
    ) == 1


def test_configured_logo_without_signature_token_is_not_loaded_or_attached(
    monkeypatch,
):
    adapter = _make_adapter(
        monkeypatch,
        {
            "rich_html_enabled": True,
            "signature": {
                "enabled": True,
                "text": "Canonical signature",
                "html": "<div>Team without logo</div>",
            },
        },
    )
    smtp = _capture_adapter_message(monkeypatch, adapter)
    loader = MagicMock(side_effect=AssertionError("logo must not be loaded"))
    monkeypatch.setattr(email_adapter, "load_signature_logo_inline_image", loader)

    adapter._send_email("user@test.com", "Message body")

    message = smtp.send_message.call_args.args[0]
    assert message.get_content_type() == "multipart/alternative"
    rendered = message.get_payload()[1].get_payload(decode=True).decode("utf-8")
    assert "<div>Team without logo</div>" in rendered
    loader.assert_not_called()


def test_signature_text_only_does_not_load_logo_or_change_plain_fallback(monkeypatch):
    adapter = _make_adapter(
        monkeypatch,
        {
            "rich_html_enabled": True,
            "signature": {
                "enabled": True,
                "text": "Text-only signature",
            },
        },
    )
    smtp = _capture_adapter_message(monkeypatch, adapter)
    loader = MagicMock(side_effect=AssertionError("logo must not be loaded"))
    monkeypatch.setattr(email_adapter, "load_signature_logo_inline_image", loader)

    adapter._send_email("user@test.com", "Message body")

    plain_part, html_part = smtp.send_message.call_args.args[0].get_payload()
    _assert_utf8_plain_part(
        plain_part,
        "Message body\n\nText-only signature",
    )
    assert "Text-only signature" in html_part.get_payload(decode=True).decode("utf-8")
    loader.assert_not_called()


@pytest.mark.parametrize(
    "extra",
    [
        {
            "rich_html_enabled": False,
            "signature": {
                "enabled": True,
                "text": "Canonical signature",
                "html": "{{email_signature_logo}}",
            },
        },
        {
            "rich_html_enabled": True,
            "signature": {
                "enabled": False,
                "text": "Disabled signature",
                "html": "{{email_signature_logo}}",
            },
        },
    ],
    ids=["rich-html-disabled", "signature-disabled"],
)
def test_disabled_feature_never_loads_signature_logo(monkeypatch, extra):
    adapter = _make_adapter(monkeypatch, extra)
    smtp = _capture_adapter_message(monkeypatch, adapter)
    loader = MagicMock(side_effect=AssertionError("logo must not be loaded"))
    monkeypatch.setattr(email_adapter, "load_signature_logo_inline_image", loader)

    adapter._send_email("user@test.com", "Message body")

    loader.assert_not_called()
    message = smtp.send_message.call_args.args[0]
    assert not any(part.get_content_disposition() == "inline" for part in message.walk())


def test_corrupt_signature_logo_is_omitted_without_blocking_gateway_send(
    monkeypatch,
    tmp_path: Path,
    caplog,
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    asset_dir = tmp_path / "profile" / "assets" / "email"
    asset_dir.mkdir(parents=True)
    (asset_dir / "signature-logo.png").write_bytes(b"corrupt")
    adapter = _make_adapter(
        monkeypatch,
        {
            "rich_html_enabled": True,
            "signature": {
                "enabled": True,
                "text": "Canonical signature",
                "html": "<div>{{email_signature_logo}}</div>",
            },
        },
    )
    smtp = _capture_adapter_message(monkeypatch, adapter)

    adapter._send_email("user@test.com", "Message body")

    message = smtp.send_message.call_args.args[0]
    assert message.get_content_type() == "multipart/alternative"
    assert "{{email_signature_logo}}" not in message.as_string()
    assert "Ignoring invalid Email signature logo" in caplog.text


def test_generated_logo_does_not_allow_manual_cid_remote_data_or_srcset(
    monkeypatch,
):
    adapter = _make_adapter(
        monkeypatch,
        {
            "rich_html_enabled": True,
            "signature": {
                "enabled": True,
                "text": "Canonical signature",
                "html": (
                    "<div>{{email_signature_logo}}"
                    '<img src="cid:user-controlled@example.test" onerror="bad()">'
                    '<img src="https://tracker.example/pixel.png">'
                    '<img src="data:image/png;base64,AAAA" '
                    'srcset="https://tracker.example/2x.png 2x"></div>'
                ),
            },
        },
    )
    smtp = _capture_adapter_message(monkeypatch, adapter)
    logo = MimeInlineImage(
        filename="signature-logo.png",
        content=b"logo",
        content_type="image/png",
    )
    monkeypatch.setattr(
        email_adapter,
        "load_signature_logo_inline_image",
        MagicMock(return_value=logo),
    )

    adapter._send_email("user@test.com", "Message body")

    rendered = _parse_message(
        smtp.send_message.call_args.args[0]
    ).get_body(preferencelist=("html",)).get_content()
    assert f'src="cid:{logo.content_id}"' in rendered
    lowered = rendered.lower()
    assert "cid:user-controlled" not in lowered
    assert "https://tracker.example" not in lowered
    assert "data:image" not in lowered
    assert "srcset" not in lowered
    assert "onerror" not in lowered


def test_signature_logo_with_attachment_uses_mixed_related_alternative_tree(
    monkeypatch,
    tmp_path: Path,
):
    adapter = _make_adapter(
        monkeypatch,
        {
            "rich_html_enabled": True,
            "signature": {
                "enabled": True,
                "text": "Canonical signature",
                "html": "<div>{{email_signature_logo}}</div>",
            },
        },
    )
    smtp = _capture_adapter_message(monkeypatch, adapter)
    logo = MimeInlineImage(
        filename="signature-logo.webp",
        content=b"logo",
        content_type="image/webp",
    )
    monkeypatch.setattr(
        email_adapter,
        "load_signature_logo_inline_image",
        MagicMock(return_value=logo),
    )
    attachment_path = tmp_path / "report.bin"
    attachment_path.write_bytes(b"report")

    adapter._send_email_with_attachment(
        "user@test.com",
        "Message body",
        str(attachment_path),
    )

    parsed = _parse_message(smtp.send_message.call_args.args[0])
    assert [part.get_content_type() for part in parsed.walk()] == [
        "multipart/mixed",
        "multipart/related",
        "multipart/alternative",
        "text/plain",
        "text/html",
        "image/webp",
        "application/octet-stream",
    ]
    related, attachment = parsed.get_payload()
    assert related.get_payload()[1]["Content-ID"] == f"<{logo.content_id}>"
    assert attachment.get_filename() == "report.bin"
    assert attachment.get_payload(decode=True) == b"report"


def test_standalone_send_expands_signature_logo_token(monkeypatch):
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    monkeypatch.setenv("EMAIL_SMTP_PORT", "587")
    smtp = MagicMock()
    monkeypatch.setattr(email_adapter.smtplib, "SMTP", MagicMock(return_value=smtp))
    monkeypatch.setattr(email_utils, "formatdate", lambda *, localtime: _DATE)
    logo = MimeInlineImage(
        filename="signature-logo.jpg",
        content=b"logo",
        content_type="image/jpeg",
    )
    monkeypatch.setattr(
        email_adapter,
        "load_signature_logo_inline_image",
        MagicMock(return_value=logo),
    )
    config = SimpleNamespace(
        token=None,
        api_key=None,
        extra={
            "address": "hermes@test.com",
            "smtp_host": "smtp.test.com",
            "rich_html_enabled": True,
            "signature": {
                "enabled": True,
                "text": "Standalone signature",
                "html": "<div>{{email_signature_logo}}</div>",
            },
        },
    )

    result = asyncio.run(
        email_adapter._standalone_send(config, "user@test.com", "Standalone body")
    )

    assert result["success"] is True
    parsed = _parse_message(smtp.send_message.call_args.args[0])
    assert [part.get_content_type() for part in parsed.walk()] == [
        "multipart/related",
        "multipart/alternative",
        "text/plain",
        "text/html",
        "image/jpeg",
    ]
    rendered = parsed.get_body(preferencelist=("html",)).get_content()
    assert f"cid:{logo.content_id}" in rendered
    assert 'width="230"' in rendered
    assert "width:230px" in rendered
    assert parsed.get_payload()[1]["Content-ID"] == f"<{logo.content_id}>"


def test_logo_token_in_message_or_plain_signature_is_never_interpreted(
    monkeypatch,
):
    adapter = _make_adapter(
        monkeypatch,
        {
            "rich_html_enabled": True,
            "signature": {
                "enabled": True,
                "text": "Plain {{email_signature_logo}} token",
            },
        },
    )
    smtp = _capture_adapter_message(monkeypatch, adapter)
    loader = MagicMock(side_effect=AssertionError("logo must not be loaded"))
    monkeypatch.setattr(email_adapter, "load_signature_logo_inline_image", loader)

    adapter._send_email(
        "user@test.com",
        "Body {{email_signature_logo}} token",
    )

    loader.assert_not_called()
    message = smtp.send_message.call_args.args[0]
    plain_part, html_part = message.get_payload()
    assert "{{email_signature_logo}}" in plain_part.get_payload(
        decode=True
    ).decode("utf-8")
    assert "{{email_signature_logo}}" in html_part.get_payload(
        decode=True
    ).decode("utf-8")
