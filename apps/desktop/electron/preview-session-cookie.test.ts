import { describe, expect, it, vi } from 'vitest'

import {
  getSunoPreviewSessionCookie,
  PREVIEW_SESSION_PARTITION,
  SUNO_SESSION_COOKIE_NAME,
  SUNO_SESSION_URL
} from './preview-session-cookie'

describe('getSunoPreviewSessionCookie', () => {
  it('reads only the expected Suno cookie from the persistent Preview partition', async () => {
    const get = vi.fn(async () => [
      { name: 'analytics', value: 'not-a-secret' },
      { name: SUNO_SESSION_COOKIE_NAME, value: 'current-session' }
    ])

    const fromPartition = vi.fn(() => ({ cookies: { get } }))

    await expect(getSunoPreviewSessionCookie(fromPartition)).resolves.toEqual({
      name: SUNO_SESSION_COOKIE_NAME,
      value: 'current-session'
    })
    expect(fromPartition).toHaveBeenCalledExactlyOnceWith(PREVIEW_SESSION_PARTITION)
    expect(get).toHaveBeenCalledExactlyOnceWith({ url: SUNO_SESSION_URL })
  })

  it('returns null instead of falling back to another cookie or a global read', async () => {
    const get = vi.fn(async () => [
      { name: '_session', value: 'wrong-name' },
      { name: SUNO_SESSION_COOKIE_NAME, value: '' }
    ])

    await expect(getSunoPreviewSessionCookie(() => ({ cookies: { get } }))).resolves.toBeNull()
    expect(get).toHaveBeenCalledExactlyOnceWith({ url: SUNO_SESSION_URL })
  })
})
