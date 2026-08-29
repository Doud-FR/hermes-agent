/** Narrow access to the Suno login carried by the persistent Preview jar. */

export const PREVIEW_SESSION_PARTITION = 'persist:hermes-preview'
export const SUNO_SESSION_URL = 'https://suno.com'
export const SUNO_SESSION_COOKIE_NAME = '__session'

interface CookieLike {
  name: string
  value: string
}

interface CookieSessionLike {
  cookies: {
    get: (filter: { url: string }) => Promise<CookieLike[]>
  }
}

export interface SunoPreviewSessionCookie {
  name: typeof SUNO_SESSION_COOKIE_NAME
  value: string
}

/**
 * Return only Suno's current session cookie from the Preview partition.
 *
 * The caller cannot choose a partition, URL, domain, or cookie name: keeping
 * those constants here prevents the renderer bridge from becoming a general
 * browser-cookie export API. Electron's cookie store includes HttpOnly values,
 * unlike document.cookie.
 */
export async function getSunoPreviewSessionCookie(
  fromPartition: (partition: string) => CookieSessionLike
): Promise<SunoPreviewSessionCookie | null> {
  const previewSession = fromPartition(PREVIEW_SESSION_PARTITION)
  const cookies = await previewSession.cookies.get({ url: SUNO_SESSION_URL })
  const cookie = cookies.find(candidate => candidate.name === SUNO_SESSION_COOKIE_NAME)

  if (!cookie?.value) {
    return null
  }

  return { name: SUNO_SESSION_COOKIE_NAME, value: cookie.value }
}
