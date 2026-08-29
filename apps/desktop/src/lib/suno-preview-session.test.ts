import { afterEach, describe, expect, it, vi } from 'vitest'

import { readSunoPreviewSession } from './suno-preview-session'

const desktopWindow = window as unknown as { hermesDesktop?: Window['hermesDesktop'] }

afterEach(() => {
  delete desktopWindow.hermesDesktop
})

describe('readSunoPreviewSession', () => {
  it('returns the narrow Electron result without adding cookie selectors', async () => {
    const getSunoSessionCookie = vi.fn(async () => ({ name: '__session' as const, value: 'current-session' }))
    desktopWindow.hermesDesktop = { getSunoSessionCookie } as unknown as Window['hermesDesktop']

    await expect(readSunoPreviewSession()).resolves.toEqual({
      cookie: { name: '__session', value: 'current-session' },
      success: true
    })
    expect(getSunoSessionCookie).toHaveBeenCalledExactlyOnceWith()
  })

  it('fails closed on an older shell or a missing session', async () => {
    desktopWindow.hermesDesktop = {} as Window['hermesDesktop']
    await expect(readSunoPreviewSession()).resolves.toMatchObject({ success: false })

    desktopWindow.hermesDesktop = {
      getSunoSessionCookie: vi.fn(async () => null)
    } as unknown as Window['hermesDesktop']
    await expect(readSunoPreviewSession()).resolves.toEqual({
      error: 'No Suno __session cookie is present. Sign in to https://suno.com in Preview first.',
      success: false
    })
  })
})
