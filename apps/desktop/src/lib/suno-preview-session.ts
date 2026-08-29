export interface SunoSessionCookieResult {
  cookie?: { name: '__session'; value: string }
  error?: string
  success: boolean
}

/** Ask Electron for the one cookie its narrow Preview bridge is allowed to expose. */
export async function readSunoPreviewSession(): Promise<SunoSessionCookieResult> {
  const read = window.hermesDesktop?.getSunoSessionCookie

  if (!read) {
    return { error: 'This Hermes Desktop build cannot read the Suno Preview session.', success: false }
  }

  try {
    const cookie = await read()

    return cookie
      ? { cookie, success: true }
      : {
          error: 'No Suno __session cookie is present. Sign in to https://suno.com in Preview first.',
          success: false
        }
  } catch (error) {
    return {
      error: `Failed to read the Suno Preview session: ${error instanceof Error ? error.message : String(error)}`,
      success: false
    }
  }
}
