import { beforeEach, describe, expect, it, vi } from 'vitest'

import { deleteEmailSignatureLogo, getEmailSignatureLogoStatus, uploadEmailSignatureLogo } from './messaging'

const { hermesApi } = vi.hoisted(() => ({ hermesApi: vi.fn() }))

vi.mock('./client', () => ({
  hermesApi: (request: unknown) => hermesApi(request),
  profileScoped: (profile?: null | string) => (profile ? { profile } : {})
}))

beforeEach(() => {
  hermesApi.mockResolvedValue({ configured: false, valid: false })
})

describe('Email signature logo API', () => {
  it('gets status within the selected profile', async () => {
    await getEmailSignatureLogoStatus('profile-a')

    expect(hermesApi).toHaveBeenCalledWith({
      path: '/api/messaging/email/signature-logo',
      profile: 'profile-a'
    })
  })

  it('uploads one multipart file with PUT and preserves the selected profile', async () => {
    const bytes = new Uint8Array([1, 2, 3]).buffer

    const file = {
      arrayBuffer: vi.fn(async () => bytes),
      name: 'chosen.webp',
      type: 'image/webp'
    } as unknown as File

    await uploadEmailSignatureLogo(file, 'profile-b')

    expect(hermesApi).toHaveBeenCalledWith({
      method: 'PUT',
      path: '/api/messaging/email/signature-logo',
      profile: 'profile-b',
      upload: {
        bytes,
        contentType: 'image/webp',
        filename: 'chosen.webp'
      }
    })
  })

  it('deletes within the selected profile', async () => {
    await deleteEmailSignatureLogo('profile-c')

    expect(hermesApi).toHaveBeenCalledWith({
      method: 'DELETE',
      path: '/api/messaging/email/signature-logo',
      profile: 'profile-c'
    })
  })
})
