import { describe, expect, it } from 'vitest'

import { EMAIL_PREVIEW_CSP, emailPreviewDocument, validateEmailPreviewResponse } from './email-preview'

const PNG_BASE64 = 'iVBORw0KGgo='

function richResponse() {
  return {
    plain_text: 'Plain fallback',
    html: '<p>Hello</p><img src="cid:logo.123@inline.invalid" alt="Logo">',
    resources: [
      {
        content_id: 'logo.123@inline.invalid',
        mime_type: 'image/png',
        data_base64: PNG_BASE64,
        size_bytes: 8,
        width: 12,
        height: 4
      }
    ]
  }
}

describe('Email preview document', () => {
  it('validates and replaces exact CID sources with validated data URLs', () => {
    const document = emailPreviewDocument(richResponse())

    expect(document).toContain(`Content-Security-Policy" content="${EMAIL_PREVIEW_CSP}`)
    expect(document).toContain(`src="data:image/png;base64,${PNG_BASE64}"`)
    expect(document).not.toContain('cid:')
    expect(document).not.toContain('allow-same-origin')
  })

  it('renders a text-only response as escaped plain text', () => {
    const document = emailPreviewDocument({ plain_text: '<b>Plain</b> & safe', html: null, resources: [] })

    expect(document).toContain('<pre>&lt;b&gt;Plain&lt;/b&gt; &amp; safe</pre>')
    expect(document).not.toContain('<b>Plain</b>')
  })

  it('removes link destinations so the sandbox has no active navigation', () => {
    const document = emailPreviewDocument({
      plain_text: 'Documentation',
      html: '<p><a href="https://example.invalid/docs" title="Docs">Documentation</a></p>',
      resources: []
    })

    expect(document).toContain('<a title="Docs">Documentation</a>')
    expect(document).not.toContain('href=')
    expect(document).not.toContain('https://example.invalid')
  })

  it.each([
    {
      name: 'non-canonical base64',
      mutate: (value: ReturnType<typeof richResponse>) => {
        value.resources[0].data_base64 = `${PNG_BASE64}\n`
      }
    },
    {
      name: 'disallowed MIME type',
      mutate: (value: ReturnType<typeof richResponse>) => {
        value.resources[0].mime_type = 'image/svg+xml'
      }
    },
    {
      name: 'resource size mismatch',
      mutate: (value: ReturnType<typeof richResponse>) => {
        value.resources[0].size_bytes = 7
      }
    },
    {
      name: 'unsafe content ID',
      mutate: (value: ReturnType<typeof richResponse>) => {
        value.resources[0].content_id = 'logo" onerror="alert(1)'
      }
    },
    {
      name: 'unreferenced resource',
      mutate: (value: ReturnType<typeof richResponse>) => {
        value.html = '<p>No image</p>'
      }
    },
    {
      name: 'unknown CID reference',
      mutate: (value: ReturnType<typeof richResponse>) => {
        value.html = '<img src="cid:other@inline.invalid">'
      }
    },
    {
      name: 'remote image source',
      mutate: (value: ReturnType<typeof richResponse>) => {
        value.resources = []
        value.html = '<img src="https://example.invalid/tracker.png">'
      }
    },
    {
      name: 'srcset attribute',
      mutate: (value: ReturnType<typeof richResponse>) => {
        value.html = '<img src="cid:logo.123@inline.invalid" srcset="https://example.invalid/tracker.png 2x">'
      }
    }
  ])('rejects $name', ({ mutate }) => {
    const value = richResponse()

    mutate(value)

    expect(() => validateEmailPreviewResponse(value)).toThrow()
  })

  it('rejects duplicate resources and resources without HTML', () => {
    const duplicate = richResponse()
    duplicate.resources.push({ ...duplicate.resources[0] })

    expect(() => validateEmailPreviewResponse(duplicate)).toThrow()
    expect(() =>
      validateEmailPreviewResponse({ plain_text: 'Plain', html: null, resources: richResponse().resources })
    ).toThrow()
  })

  it('uses a restrictive no-network, no-navigation policy', () => {
    for (const directive of [
      "default-src 'none'",
      "script-src 'none'",
      "connect-src 'none'",
      'img-src data:',
      "object-src 'none'",
      "frame-src 'none'",
      "base-uri 'none'",
      "form-action 'none'",
      "navigate-to 'none'"
    ]) {
      expect(EMAIL_PREVIEW_CSP).toContain(directive)
    }
  })
})
