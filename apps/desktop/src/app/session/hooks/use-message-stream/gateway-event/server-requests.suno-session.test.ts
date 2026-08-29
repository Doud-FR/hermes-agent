import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createClientSessionState } from '@/lib/chat-runtime'

import { handleServerRequest, type ServerRequestContext } from './server-requests'

const desktopWindow = window as unknown as { hermesDesktop?: Window['hermesDesktop'] }

const deps = {
  activeSessionIdRef: { current: null },
  sessionInterrupted: () => false,
  updateSessionState: (_sessionId, update) => update(createClientSessionState('stored-session')),
  upsertToolCall: () => undefined
} as ServerRequestContext['deps']

function deliver(activeSessionId: string) {
  const respond = vi.fn()
  const fail = vi.fn()
  const handled = handleServerRequest(
    {
      fail,
      id: 'request-1',
      method: 'preview.act',
      params: { action: 'suno_session', session_id: 'runtime-1' },
      profile: 'default',
      respond
    },
    deps,
    activeSessionId
  )

  return { fail, handled, respond }
}

describe('Suno Preview session request', () => {
  const getSunoSessionCookie = vi.fn(async () => ({ name: '__session' as const, value: 'current-session' }))

  beforeEach(() => {
    getSunoSessionCookie.mockClear()
    desktopWindow.hermesDesktop = { getSunoSessionCookie } as unknown as Window['hermesDesktop']
  })

  afterEach(() => {
    delete desktopWindow.hermesDesktop
  })

  it('answers an active desktop session through the fixed Electron capability', async () => {
    const { handled, respond } = deliver('runtime-1')

    expect(handled).toBe(true)
    await vi.waitFor(() => expect(respond).toHaveBeenCalledOnce())
    expect(getSunoSessionCookie).toHaveBeenCalledExactlyOnceWith()
    expect(respond).toHaveBeenCalledWith({
      value: JSON.stringify({
        cookie: { name: '__session', value: 'current-session' },
        success: true
      })
    })
  })

  it('leaves a background session unanswered before touching Electron', () => {
    const { handled, respond } = deliver('another-session')

    expect(handled).toBe(true)
    expect(getSunoSessionCookie).not.toHaveBeenCalled()
    expect(respond).not.toHaveBeenCalled()
  })
})
