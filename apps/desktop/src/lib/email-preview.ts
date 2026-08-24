import type { MessagingEmailPreviewResource, MessagingEmailPreviewResponse } from '@/types/hermes'

const ALLOWED_MIME_TYPES = new Set(['image/gif', 'image/jpeg', 'image/png', 'image/webp'])
const MAX_RESOURCE_BYTES = 2 * 1024 * 1024
const MAX_RESOURCES = 4
const CONTENT_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9.@_-]{0,254}$/
const BASE64_PATTERN = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/
const CID_SOURCE_PATTERN = /\bsrc=(['"])cid:([^'"<>\s]+)\1/gi
const SOURCE_ATTRIBUTE_PATTERN = /\bsrc=(['"])([^'"]*)\1/gi
const SRCSET_ATTRIBUTE_PATTERN = /\bsrcset\s*=/i
const LINK_DESTINATION_PATTERN = /\s+href=(['"])[^'"]*\1/gi

export const EMAIL_PREVIEW_CSP = [
  "default-src 'none'",
  "script-src 'none'",
  "connect-src 'none'",
  'img-src data:',
  "style-src 'unsafe-inline'",
  "font-src 'none'",
  "media-src 'none'",
  "object-src 'none'",
  "frame-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "navigate-to 'none'"
].join('; ')

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function canonicalBase64Bytes(value: unknown, expectedSize: number): string | null {
  if (typeof value !== 'string' || value.length > Math.ceil(MAX_RESOURCE_BYTES / 3) * 4 + 4) {
    return null
  }

  if (!BASE64_PATTERN.test(value)) {
    return null
  }

  try {
    const decoded = atob(value)

    return decoded.length === expectedSize && btoa(decoded) === value ? value : null
  } catch {
    return null
  }
}

function validateResource(value: unknown): MessagingEmailPreviewResource {
  if (!isRecord(value)) {
    throw new Error('Invalid Email preview resource')
  }

  const { content_id, data_base64, height, mime_type, size_bytes, width } = value

  if (
    typeof content_id !== 'string' ||
    !CONTENT_ID_PATTERN.test(content_id) ||
    typeof mime_type !== 'string' ||
    !ALLOWED_MIME_TYPES.has(mime_type) ||
    !Number.isInteger(size_bytes) ||
    (size_bytes as number) <= 0 ||
    (size_bytes as number) > MAX_RESOURCE_BYTES ||
    !Number.isInteger(width) ||
    (width as number) <= 0 ||
    (width as number) > 4096 ||
    !Number.isInteger(height) ||
    (height as number) <= 0 ||
    (height as number) > 4096 ||
    !canonicalBase64Bytes(data_base64, size_bytes as number)
  ) {
    throw new Error('Invalid Email preview resource')
  }

  return {
    content_id,
    data_base64: data_base64 as string,
    height: height as number,
    mime_type: mime_type as MessagingEmailPreviewResource['mime_type'],
    size_bytes: size_bytes as number,
    width: width as number
  }
}

export function validateEmailPreviewResponse(value: unknown): MessagingEmailPreviewResponse {
  if (!isRecord(value) || typeof value.plain_text !== 'string') {
    throw new Error('Invalid Email preview response')
  }

  if (value.html !== null && typeof value.html !== 'string') {
    throw new Error('Invalid Email preview response')
  }

  if (!Array.isArray(value.resources) || value.resources.length > MAX_RESOURCES) {
    throw new Error('Invalid Email preview response')
  }

  const resources = value.resources.map(validateResource)
  const totalBytes = resources.reduce((total, resource) => total + resource.size_bytes, 0)
  const contentIds = new Set(resources.map(resource => resource.content_id))

  if (totalBytes > MAX_RESOURCE_BYTES || contentIds.size !== resources.length) {
    throw new Error('Invalid Email preview resources')
  }

  if (value.html === null && resources.length > 0) {
    throw new Error('Email preview resources require HTML')
  }

  const referencedContentIds = new Set<string>()

  if (typeof value.html === 'string') {
    if (SRCSET_ATTRIBUTE_PATTERN.test(value.html)) {
      throw new Error('Email preview HTML contains an unsupported source')
    }

    for (const match of value.html.matchAll(SOURCE_ATTRIBUTE_PATTERN)) {
      if (!match[2].startsWith('cid:')) {
        throw new Error('Email preview HTML contains an unsupported source')
      }
    }

    for (const match of value.html.matchAll(CID_SOURCE_PATTERN)) {
      referencedContentIds.add(match[2])
    }
  }

  if (
    [...referencedContentIds].some(contentId => !contentIds.has(contentId)) ||
    resources.some(resource => !referencedContentIds.has(resource.content_id))
  ) {
    throw new Error('Email preview CID resources do not match the HTML')
  }

  return {
    plain_text: value.plain_text,
    html: value.html as null | string,
    resources
  }
}

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

export function emailPreviewDocument(value: unknown): string {
  const preview = validateEmailPreviewResponse(value)
  const resources = new Map(preview.resources.map(resource => [resource.content_id, resource]))

  const renderedFragment =
    preview.html === null
      ? `<pre>${escapeHtml(preview.plain_text)}</pre>`
      : preview.html.replace(CID_SOURCE_PATTERN, (_match, quote: string, contentId: string) => {
          const resource = resources.get(contentId)

          if (!resource) {
            throw new Error('Email preview CID resource is missing')
          }

          return `src=${quote}data:${resource.mime_type};base64,${resource.data_base64}${quote}`
        })

  const fragment = renderedFragment.replace(LINK_DESTINATION_PATTERN, '')

  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${EMAIL_PREVIEW_CSP}"><meta name="referrer" content="no-referrer"><style>html{color-scheme:light}body{margin:16px;background:#fff;color:#171717;font-family:Arial,sans-serif;font-size:14px;line-height:1.5;overflow-wrap:anywhere}img{max-width:100%;height:auto}a{pointer-events:none}pre{white-space:pre-wrap;font:inherit}</style></head><body>${fragment}</body></html>`
}
