import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $gateway } from '@/store/gateway'

import { handleDesktopBridgeEvent } from './desktop-bridge'
import type { GatewayEventContext } from './types'

const desktopWindow = window as unknown as { hermesDesktop?: Window['hermesDesktop'] }

function requestEvent(isActiveEvent: boolean): GatewayEventContext {
  return {
    event: { session_id: 'runtime-1', type: 'preview.act.request' },
    isActiveEvent,
    payload: { action: 'suno_session', request_id: 'request-1' }
  } as unknown as GatewayEventContext
}

describe('Suno Preview session bridge', () => {
  const request = vi.fn(async () => null)
  const getSunoSessionCookie = vi.fn(async () => ({ name: '__session' as const, value: 'current-session' }))

  beforeEach(() => {
    request.mockClear()
    getSunoSessionCookie.mockClear()
    $gateway.set({ request } as never)
    desktopWindow.hermesDesktop = { getSunoSessionCookie } as unknown as Window['hermesDesktop']
  })

  afterEach(() => {
    $gateway.set(null)
    delete desktopWindow.hermesDesktop
  })

  it('answers an active desktop session through the fixed Electron capability', async () => {
    expect(handleDesktopBridgeEvent(requestEvent(true))).toBe(true)

    await vi.waitFor(() => expect(request).toHaveBeenCalledOnce())
    expect(getSunoSessionCookie).toHaveBeenCalledExactlyOnceWith()
    expect(request).toHaveBeenCalledWith('preview.act.respond', {
      request_id: 'request-1',
      text: JSON.stringify({
        cookie: { name: '__session', value: 'current-session' },
        success: true
      })
    })
  })

  it('refuses a background session before touching Electron', async () => {
    expect(handleDesktopBridgeEvent(requestEvent(false))).toBe(true)

    await vi.waitFor(() => expect(request).toHaveBeenCalledOnce())
    expect(getSunoSessionCookie).not.toHaveBeenCalled()
    expect(request).toHaveBeenCalledWith(
      'preview.act.respond',
      expect.objectContaining({ request_id: 'request-1', text: expect.stringContaining('only takes actions') })
    )
  })
})
