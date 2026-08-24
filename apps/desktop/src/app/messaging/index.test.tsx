// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { MessagingPlatformInfo } from '@/types/hermes'

const getMessagingPlatforms = vi.fn()
const getEmailSignatureLogoStatus = vi.fn()
const uploadEmailSignatureLogo = vi.fn()
const deleteEmailSignatureLogo = vi.fn()
const previewEmail = vi.fn()
const updateMessagingPlatform = vi.fn()
const getPairing = vi.fn()
const approvePairing = vi.fn()
const revokePairing = vi.fn()
const openExternalLink = vi.fn()

vi.mock('@/hermes', () => ({
  approvePairing: (platformId: string, requestId: string, profile?: null | string) =>
    approvePairing(platformId, requestId, profile),
  deleteEmailSignatureLogo: (profile?: null | string) => deleteEmailSignatureLogo(profile),
  getEmailSignatureLogoStatus: (profile?: null | string) => getEmailSignatureLogoStatus(profile),
  getMessagingPlatforms: (profile?: null | string) => getMessagingPlatforms(profile),
  getPairing: (profile?: null | string) => getPairing(profile),
  getProfiles: vi.fn(async () => ({ profiles: [] })),
  revokePairing: (platformId: string, userId: string, profile?: null | string) =>
    revokePairing(platformId, userId, profile),
  previewEmail: (body: unknown, profile?: null | string) => previewEmail(body, profile),
  setApiRequestProfile: vi.fn(),
  uploadEmailSignatureLogo: (file: File, profile?: null | string) => uploadEmailSignatureLogo(file, profile),
  updateMessagingPlatform: (id: string, body: unknown, profile?: null | string) =>
    updateMessagingPlatform(id, body, profile)
}))

// Keep store/profile's side-effecting imports inert (pulled in via the shared
// settings scope store) — same seam as store/profile.test.ts.
vi.mock('@/store/gateway', () => ({
  $gateway: { get: () => null, subscribe: () => () => {} },
  ensureGatewayForAgent: vi.fn(async () => undefined),
  ensureGatewayForProfile: vi.fn(async () => undefined),
  openGatewayForProfile: vi.fn(async () => undefined)
}))
vi.mock('@/lib/query-client', () => ({ invalidateProfileScopedQueries: vi.fn() }))
vi.mock('@/store/starmap', () => ({ resetStarmapGraph: vi.fn() }))

vi.mock('@/lib/external-link', () => ({
  openExternalLink: (href: string) => openExternalLink(href)
}))

vi.mock('@/store/notifications', () => ({
  notify: vi.fn(),
  notifyError: vi.fn()
}))

vi.mock('@/store/system-actions', () => ({
  runGatewayRestart: vi.fn()
}))

function platform(patch: Partial<MessagingPlatformInfo> = {}): MessagingPlatformInfo {
  return {
    configured: false,
    description: 'A platform.',
    docs_url: '',
    enabled: false,
    env_vars: [],
    gateway_running: true,
    id: 'teams',
    name: 'Microsoft Teams',
    state: 'disabled',
    ...patch
  }
}

function emailPlatform(patch: Partial<MessagingPlatformInfo> = {}): MessagingPlatformInfo {
  return platform({
    configured: true,
    config: {
      rich_html_enabled: false,
      signature: { enabled: false, html: '', logo_width: 230, text: '' }
    },
    id: 'email',
    name: 'Email',
    ...patch
  })
}

const noLogo = {
  configured: false,
  format: null,
  height: null,
  mime_type: null,
  modified_at: null,
  size_bytes: null,
  valid: false,
  width: null
}

const pngLogo = {
  configured: true,
  format: 'PNG',
  height: 64,
  mime_type: 'image/png',
  modified_at: '2026-08-24T10:00:00Z',
  size_bytes: 1536,
  valid: true,
  width: 128
}

beforeEach(() => {
  updateMessagingPlatform.mockResolvedValue({ ok: true, platform: 'teams' })
  getPairing.mockResolvedValue({ approved: [], pending: [] })
  getEmailSignatureLogoStatus.mockResolvedValue(noLogo)
  uploadEmailSignatureLogo.mockResolvedValue(pngLogo)
  deleteEmailSignatureLogo.mockResolvedValue(noLogo)
  previewEmail.mockResolvedValue({
    plain_text: 'Hello there.',
    html: '<p>Hello there.</p>',
    resources: []
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderMessaging() {
  const { MessagingView } = await import('./index')
  let result: ReturnType<typeof render>
  await act(async () => {
    result = render(
      <MemoryRouter>
        <MessagingView />
      </MemoryRouter>
    )
  })

  return result!
}

describe('MessagingView profile scope', () => {
  it('follows the active profile instead of targeting primary when there is no override', async () => {
    const { $settingsScopeOverride } = await import('@/store/settings-scope')

    $settingsScopeOverride.set(null)
    getMessagingPlatforms.mockResolvedValue({ platforms: [platform()] })

    await renderMessaging()

    await waitFor(() => expect(getMessagingPlatforms).toHaveBeenCalledWith(undefined))
    expect(getPairing).toHaveBeenCalledWith(undefined)
  })
})

describe('MessagingView setup-guide link', () => {
  it('hides the setup-guide button for a plugin platform with no docs URL', async () => {
    // Teams (and other plugin platforms) ship an empty docs_url. Rendering an
    // anchor with href="" let Electron resolve it to the app's own packaged
    // index.html and fail with an OS "file not found" dialog. The button must
    // simply not appear when there is no guide to open.
    getMessagingPlatforms.mockResolvedValue({ platforms: [platform({ docs_url: '' })] })

    await renderMessaging()

    expect((await screen.findAllByText('Microsoft Teams')).length).toBeGreaterThan(0)
    expect(screen.queryByText('Open setup guide')).toBeNull()
  })

  it('opens a real docs URL through the validated external opener', async () => {
    const docsUrl = 'https://hermes-agent.nousresearch.com/docs/user-guide/messaging/teams'
    getMessagingPlatforms.mockResolvedValue({ platforms: [platform({ docs_url: docsUrl })] })

    await renderMessaging()

    const link = await screen.findByText('Open setup guide')
    await act(async () => {
      fireEvent.click(link)
    })

    await waitFor(() => expect(openExternalLink).toHaveBeenCalledWith(docsUrl))
  })
})

describe('MessagingView email content settings', () => {
  it('reads the typed email config and renders both signature fields', async () => {
    getMessagingPlatforms.mockResolvedValue({
      platforms: [
        emailPlatform({
          config: {
            rich_html_enabled: true,
            signature: {
              enabled: true,
              html: '<strong>Advanced signature</strong>',
              logo_width: 230,
              text: 'Plain signature'
            }
          }
        })
      ]
    })

    await renderMessaging()

    expect((await screen.findByRole('switch', { name: 'Rich HTML email' })).getAttribute('data-state')).toBe('checked')
    expect(screen.getByRole('switch', { name: 'Signature' }).getAttribute('data-state')).toBe('checked')
    expect((screen.getByLabelText('Signature text') as HTMLTextAreaElement).value).toBe('Plain signature')
    expect((screen.getByLabelText('Advanced signature HTML') as HTMLTextAreaElement).value).toBe(
      '<strong>Advanced signature</strong>'
    )
  })

  it('writes a typed config update without trimming signature content', async () => {
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })
    updateMessagingPlatform.mockResolvedValue({ ok: true, platform: 'email' })

    await renderMessaging()

    fireEvent.click(await screen.findByRole('switch', { name: 'Rich HTML email' }))
    fireEvent.click(screen.getByRole('switch', { name: 'Signature' }))
    fireEvent.change(screen.getByLabelText('Signature text'), {
      target: { value: '  Generic assistant\nSupport  ' }
    })
    fireEvent.change(screen.getByLabelText('Advanced signature HTML'), {
      target: { value: '<strong>Generic assistant</strong>' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() =>
      expect(updateMessagingPlatform).toHaveBeenCalledWith(
        'email',
        {
          config: {
            rich_html_enabled: true,
            signature: {
              enabled: true,
              html: '<strong>Generic assistant</strong>',
              logo_width: 230,
              text: '  Generic assistant\nSupport  '
            }
          }
        },
        undefined
      )
    )
  })

  it('requires the canonical plain-text fallback before saving', async () => {
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })

    await renderMessaging()

    fireEvent.click(await screen.findByRole('switch', { name: 'Signature' }))

    expect(screen.getByText('Signature text is required when the signature is enabled.')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Save changes' }) as HTMLButtonElement).disabled).toBe(true)
    expect(updateMessagingPlatform).not.toHaveBeenCalled()
  })

  it('keeps the existing page compatible with a backend that omits email config', async () => {
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform({ config: undefined })] })

    await renderMessaging()

    expect((await screen.findAllByText('Email')).length).toBeGreaterThan(0)
    expect(screen.queryByRole('switch', { name: 'Rich HTML email' })).toBeNull()
    expect(screen.queryByRole('switch', { name: 'Signature' })).toBeNull()
  })

  it('shows the empty logo state and its activation contract without changing signature settings', async () => {
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })

    await renderMessaging()

    expect(await screen.findByText('No signature logo configured.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Choose logo' })).toBeTruthy()
    expect(screen.getByText(/\{\{email_signature_logo\}\}/)).toBeTruthy()
    expect(screen.getByRole('switch', { name: 'Signature' }).getAttribute('data-state')).toBe('unchecked')
  })

  it('renders configured metadata but never renders an unexpected backend path', async () => {
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })
    getEmailSignatureLogoStatus.mockResolvedValue({
      ...pngLogo,
      path: 'C:\\Users\\someone\\.hermes\\assets\\email\\signature-logo.png'
    })

    await renderMessaging()

    expect(await screen.findByText('Signature logo configured.')).toBeTruthy()
    expect(screen.getByText('PNG · 128 × 64 · 1.5 KiB')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Replace logo' })).toBeTruthy()
    expect(screen.queryByText(/C:\\Users\\someone/)).toBeNull()
  })

  it('uploads and refreshes status while preserving the edited signature fields', async () => {
    getMessagingPlatforms.mockResolvedValue({
      platforms: [
        emailPlatform({
          config: {
            rich_html_enabled: true,
            signature: { enabled: true, html: '<strong>Keep HTML</strong>', logo_width: 230, text: 'Keep text' }
          }
        })
      ]
    })
    getEmailSignatureLogoStatus.mockResolvedValueOnce(noLogo).mockResolvedValue(pngLogo)
    const file = new File(['logo'], 'chosen.png', { type: 'image/png' })

    await renderMessaging()
    await screen.findByText('No signature logo configured.')
    fireEvent.change(screen.getByLabelText('Signature logo file'), { target: { files: [file] } })

    await waitFor(() => expect(uploadEmailSignatureLogo).toHaveBeenCalledWith(file, undefined))
    await waitFor(() => expect(getEmailSignatureLogoStatus).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('Signature logo uploaded.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Replace logo' })).toBeTruthy()
    expect((screen.getByLabelText('Signature text') as HTMLTextAreaElement).value).toBe('Keep text')
    expect((screen.getByLabelText('Advanced signature HTML') as HTMLTextAreaElement).value).toBe(
      '<strong>Keep HTML</strong>'
    )
    expect(updateMessagingPlatform).not.toHaveBeenCalled()
  })

  it('replaces an existing logo through the same upload operation', async () => {
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })
    getEmailSignatureLogoStatus.mockResolvedValue(pngLogo)
    const file = new File(['replacement'], 'replacement.webp', { type: 'image/webp' })

    await renderMessaging()
    await screen.findByText('Signature logo configured.')
    fireEvent.change(screen.getByLabelText('Signature logo file'), { target: { files: [file] } })

    await waitFor(() => expect(uploadEmailSignatureLogo).toHaveBeenCalledWith(file, undefined))
  })

  it('shows a busy state while an upload is in progress', async () => {
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })
    let finishUpload!: (value: typeof pngLogo) => void
    uploadEmailSignatureLogo.mockReturnValue(
      new Promise(resolve => {
        finishUpload = resolve
      })
    )

    await renderMessaging()
    await screen.findByText('No signature logo configured.')
    fireEvent.change(screen.getByLabelText('Signature logo file'), {
      target: { files: [new File(['logo'], 'chosen.png', { type: 'image/png' })] }
    })

    expect(((await screen.findByRole('button', { name: 'Uploading…' })) as HTMLButtonElement).disabled).toBe(true)

    await act(async () => finishUpload(pngLogo))
    expect(await screen.findByText('Signature logo uploaded.')).toBeTruthy()
  })

  it('rejects oversized files before calling the backend', async () => {
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })
    const file = new File([new Uint8Array(2 * 1024 * 1024 + 1)], 'too-large.png', { type: 'image/png' })

    await renderMessaging()
    await screen.findByText('No signature logo configured.')
    fireEvent.change(screen.getByLabelText('Signature logo file'), { target: { files: [file] } })

    expect(await screen.findByText('The logo must be 2 MiB or smaller.')).toBeTruthy()
    expect(uploadEmailSignatureLogo).not.toHaveBeenCalled()
  })

  it('rejects a clearly incompatible browser MIME type before upload', async () => {
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })
    const file = new File(['not an image'], 'notes.txt', { type: 'text/plain' })

    await renderMessaging()
    await screen.findByText('No signature logo configured.')
    fireEvent.change(screen.getByLabelText('Signature logo file'), { target: { files: [file] } })

    expect(await screen.findByText('Choose a PNG, JPEG, GIF, or WebP image.')).toBeTruthy()
    expect(uploadEmailSignatureLogo).not.toHaveBeenCalled()
  })

  it.each([
    ['413', 'The logo must be 2 MiB or smaller.'],
    ['422', 'The backend rejected this image. Choose a valid PNG, JPEG, GIF, or WebP file.']
  ])('shows a specific message for backend %s errors', async (status, message) => {
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })
    uploadEmailSignatureLogo.mockRejectedValue(new Error(`${status}: rejected`))

    await renderMessaging()
    await screen.findByText('No signature logo configured.')
    fireEvent.change(screen.getByLabelText('Signature logo file'), {
      target: { files: [new File(['bad'], 'bad.png', { type: 'image/png' })] }
    })

    expect(await screen.findByText(message)).toBeTruthy()
  })

  it('surfaces the existing OAuth remote-upload limitation without breaking the section', async () => {
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })
    uploadEmailSignatureLogo.mockRejectedValue(
      new Error('File uploads are not supported against OAuth-gated remote backends yet.')
    )

    await renderMessaging()
    await screen.findByText('No signature logo configured.')
    fireEvent.change(screen.getByLabelText('Signature logo file'), {
      target: { files: [new File(['logo'], 'chosen.png', { type: 'image/png' })] }
    })

    expect(
      await screen.findByText('File uploads are not supported against OAuth-gated remote backends yet.')
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Choose logo' })).toBeTruthy()
  })

  it('confirms deletion, refreshes status, and preserves signature fields', async () => {
    getMessagingPlatforms.mockResolvedValue({
      platforms: [
        emailPlatform({
          config: {
            rich_html_enabled: true,
            signature: { enabled: true, html: '<em>Still here</em>', logo_width: 230, text: 'Still here' }
          }
        })
      ]
    })
    getEmailSignatureLogoStatus.mockResolvedValueOnce(pngLogo).mockResolvedValue(noLogo)

    await renderMessaging()
    fireEvent.click(await screen.findByRole('button', { name: 'Remove logo' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove logo' }))

    await waitFor(() => expect(deleteEmailSignatureLogo).toHaveBeenCalledWith(undefined))
    await waitFor(() => expect(getEmailSignatureLogoStatus).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('Signature logo removed.')).toBeTruthy()
    expect((screen.getByLabelText('Signature text') as HTMLTextAreaElement).value).toBe('Still here')
    expect((screen.getByLabelText('Advanced signature HTML') as HTMLTextAreaElement).value).toBe('<em>Still here</em>')
    expect(updateMessagingPlatform).not.toHaveBeenCalled()
  })

  it('degrades cleanly when an older backend does not expose the logo endpoint', async () => {
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })
    getEmailSignatureLogoStatus.mockRejectedValue(new Error('404: {"detail":"Not Found"}'))

    await renderMessaging()

    expect(await screen.findByText('Signature logos require a newer Hermes backend.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Choose logo' })).toBeNull()
    expect(screen.getByRole('switch', { name: 'Rich HTML email' })).toBeTruthy()
  })

  it('reports a missing selected profile instead of treating it as an old backend', async () => {
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })
    getEmailSignatureLogoStatus.mockRejectedValue(new Error('404: {"detail":"Profile profile-a does not exist"}'))

    await renderMessaging()

    expect(await screen.findByText('The selected profile was not found.')).toBeTruthy()
    expect(screen.queryByText('Signature logos require a newer Hermes backend.')).toBeNull()
  })

  it('reloads and isolates status when the selected profile changes', async () => {
    const { $settingsScopeOverride } = await import('@/store/settings-scope')
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })
    getEmailSignatureLogoStatus.mockImplementation(async profile => (profile === 'profile-a' ? pngLogo : { ...noLogo }))

    try {
      $settingsScopeOverride.set('profile-a')
      await renderMessaging()
      expect(await screen.findByText('Signature logo configured.')).toBeTruthy()
      expect(getEmailSignatureLogoStatus).toHaveBeenCalledWith('profile-a')

      act(() => $settingsScopeOverride.set('profile-b'))

      expect(await screen.findByText('No signature logo configured.')).toBeTruthy()
      expect(getEmailSignatureLogoStatus).toHaveBeenCalledWith('profile-b')
    } finally {
      act(() => $settingsScopeOverride.set(null))
    }
  })

  it('generates a sandboxed preview from current unsaved Email settings', async () => {
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })

    const rendered = await renderMessaging()
    fireEvent.click(await screen.findByRole('switch', { name: 'Rich HTML email' }))
    fireEvent.click(screen.getByRole('switch', { name: 'Signature' }))
    fireEvent.change(screen.getByLabelText('Signature text'), { target: { value: 'Unsaved fallback' } })
    fireEvent.change(screen.getByLabelText('Advanced signature HTML'), {
      target: { value: '<strong>Unsaved HTML</strong>' }
    })
    fireEvent.change(screen.getByLabelText('Sample message'), { target: { value: 'Preview **Markdown**' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate preview' }))

    await waitFor(() =>
      expect(previewEmail).toHaveBeenCalledWith(
        {
          body_markdown: 'Preview **Markdown**',
          config: {
            rich_html_enabled: true,
            signature: {
              enabled: true,
              html: '<strong>Unsaved HTML</strong>',
              logo_width: 230,
              text: 'Unsaved fallback'
            }
          }
        },
        undefined
      )
    )
    const frame = rendered.container.querySelector('iframe[title="Rendered Email preview"]')

    expect(frame?.getAttribute('sandbox')).toBe('')
    expect(frame?.getAttribute('referrerpolicy')).toBe('no-referrer')
    expect(frame?.getAttribute('srcdoc')).toContain("default-src 'none'")
    expect(frame?.getAttribute('srcdoc')).toContain('<p>Hello there.</p>')
    expect(screen.getByText('Hello there.')).toBeTruthy()
    expect(updateMessagingPlatform).not.toHaveBeenCalled()
  })

  it('invalidates a generated preview when its sample or settings change', async () => {
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })

    const rendered = await renderMessaging()
    fireEvent.click(await screen.findByRole('button', { name: 'Generate preview' }))
    await waitFor(() => expect(rendered.container.querySelector('iframe')).not.toBeNull())

    fireEvent.change(screen.getByLabelText('Sample message'), { target: { value: 'Changed' } })

    await waitFor(() => expect(rendered.container.querySelector('iframe')).toBeNull())
  })

  it('degrades cleanly when an older backend does not expose preview', async () => {
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })
    previewEmail.mockRejectedValue(new Error('404: {"detail":"Not Found"}'))

    await renderMessaging()
    fireEvent.click(await screen.findByRole('button', { name: 'Generate preview' }))

    expect(await screen.findByText('Email preview requires a newer Hermes backend.')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Generate preview' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByRole('switch', { name: 'Rich HTML email' })).toBeTruthy()
  })

  it.each(['400', '422', '500'])('shows a safe preview error for backend %s responses', async status => {
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })
    previewEmail.mockRejectedValue(new Error(`${status}: preview failed`))

    await renderMessaging()
    fireEvent.click(await screen.findByRole('button', { name: 'Generate preview' }))

    expect(await screen.findByText('Could not generate the Email preview.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Generate preview' })).toBeTruthy()
  })

  it('reloads preview scope and clears prior output when the profile changes', async () => {
    const { $settingsScopeOverride } = await import('@/store/settings-scope')
    getMessagingPlatforms.mockResolvedValue({ platforms: [emailPlatform()] })

    try {
      $settingsScopeOverride.set('profile-a')
      const rendered = await renderMessaging()
      fireEvent.click(await screen.findByRole('button', { name: 'Generate preview' }))
      await waitFor(() => expect(previewEmail).toHaveBeenCalledWith(expect.anything(), 'profile-a'))
      await waitFor(() => expect(rendered.container.querySelector('iframe')).not.toBeNull())

      act(() => $settingsScopeOverride.set('profile-b'))

      await waitFor(() => expect(rendered.container.querySelector('iframe')).toBeNull())
      fireEvent.click(screen.getByRole('button', { name: 'Generate preview' }))
      await waitFor(() => expect(previewEmail).toHaveBeenCalledWith(expect.anything(), 'profile-b'))
    } finally {
      act(() => $settingsScopeOverride.set(null))
    }
  })
})

describe('MessagingView pairing', () => {
  const pendingUser = {
    age_minutes: 3,
    platform: 'teams',
    request_id: 'a1b2c3d4e5f60718',
    user_id: '7712345',
    user_name: 'Bee'
  }

  it('approves the listed request by its request id, never by a code', async () => {
    // The whole point of the request-id grant path: the UI can only ever send
    // the server-side row id, because the one-time code is never returned by
    // the API. Posting anything derived from the code could not be approved.
    getMessagingPlatforms.mockResolvedValue({ platforms: [platform()] })
    getPairing.mockResolvedValue({ approved: [], pending: [pendingUser] })
    approvePairing.mockResolvedValue({ ok: true, user: { user_id: '7712345', user_name: 'Bee' } })

    await renderMessaging()

    const approve = await screen.findByRole('button', { name: 'Approve' })
    await act(async () => {
      fireEvent.click(approve)
    })

    await waitFor(() => expect(approvePairing).toHaveBeenCalledWith('teams', 'a1b2c3d4e5f60718', undefined))
  })

  it('restores the pending row when approval fails', async () => {
    // Optimistic removal must not silently swallow the request: a failed
    // approve has to leave the operator something to retry.
    getMessagingPlatforms.mockResolvedValue({ platforms: [platform()] })
    getPairing.mockResolvedValue({ approved: [], pending: [pendingUser] })
    approvePairing.mockRejectedValue(new Error('500 boom'))

    await renderMessaging()

    await act(async () => {
      fireEvent.click(await screen.findByRole('button', { name: 'Approve' }))
    })

    expect(await screen.findByRole('button', { name: 'Approve' })).toBeTruthy()
    expect(screen.getByText('Bee')).toBeTruthy()
  })

  it('shows no pairing affordance when nobody is waiting', async () => {
    // Approvals are rare; an always-present empty state would be permanent
    // chrome on a page that is otherwise about credentials.
    getMessagingPlatforms.mockResolvedValue({ platforms: [platform()] })
    getPairing.mockResolvedValue({ approved: [], pending: [] })

    await renderMessaging()

    expect((await screen.findAllByText('Microsoft Teams')).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull()
    expect(screen.queryByText(/Pending requests/)).toBeNull()
  })

  it('still renders platforms when the pairing endpoint fails', async () => {
    // An older backend without the endpoint must not blank the page.
    getMessagingPlatforms.mockResolvedValue({ platforms: [platform()] })
    getPairing.mockRejectedValue(new Error('404 not found'))

    await renderMessaging()

    expect((await screen.findAllByText('Microsoft Teams')).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull()
  })

  it('refetches pending rows on pairing.changed, not on platforms.changed', async () => {
    // The two signals are not interchangeable: platforms.changed tracks
    // connect/disconnect health via gateway_state.json, which a new pairing
    // request never moves. Riding it would leave someone invisible in the
    // pending list until an unrelated reconnect happened to fire.
    const { $changeEventsAvailable, $pairingChangeTick, $platformsChangeTick } = await import('@/store/live-sync')

    getMessagingPlatforms.mockResolvedValue({ platforms: [platform()] })
    getPairing.mockResolvedValue({ approved: [], pending: [] })

    await renderMessaging()
    await act(async () => {
      $changeEventsAvailable.set(true)
    })
    getPairing.mockClear()

    // Someone DMs the bot: the store moves, the watcher ticks pairing.changed.
    getPairing.mockResolvedValue({ approved: [], pending: [pendingUser] })
    await act(async () => {
      $pairingChangeTick.set($pairingChangeTick.get() + 1)
    })

    await waitFor(() => expect(getPairing).toHaveBeenCalled())
    expect(await screen.findByRole('button', { name: 'Approve' })).toBeTruthy()

    // A platform health tick alone must not be what fetches pairing.
    getPairing.mockClear()
    await act(async () => {
      $platformsChangeTick.set($platformsChangeTick.get() + 1)
    })
    expect(getPairing).not.toHaveBeenCalled()
  })
})
