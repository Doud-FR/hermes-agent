# Suno session recovery from Desktop Preview

Hermes Desktop exposes one narrow credential-recovery action for a Suno API
client. It does not expose a general cookie reader.

## Agent call

When a Suno API request returns `401`, the active Hermes session calls:

```json
{"action":"suno_session"}
```

through `drive_preview`. The response is either:

```json
{"success":true,"cookie":{"name":"__session","value":"..."}}
```

or a failure with an `error` string. Treat `cookie.value` as a secret: use it
only to build `Cookie: __session=<value>` for `https://suno.com`, and never
print, log, persist, or forward it elsewhere.

The client should retry the failed request once with the refreshed cookie. If
that retry also returns `401`, stop and ask the user to sign in to Suno again in
Desktop Preview; do not loop.

For the current local `suno_client.py`, this orchestration belongs to the
Hermes Agent rather than the standalone Python process: `_check()` reports the
`HTTP 401`, the Agent obtains a fresh cookie through `drive_preview`, creates a
new `Suno(cookie.value)`, and repeats the requested operation once. The Python
script by itself has no Electron channel and therefore cannot refresh the
cookie when run outside Hermes. Adding a localhost or named-pipe cookie server
solely for that case would broaden the secret boundary and is intentionally not
part of this implementation.

## Call path and boundary

1. `drive_preview` sends `preview.act.request` with `action=suno_session`.
2. The active Desktop renderer calls `window.hermesDesktop.getSunoSessionCookie()`.
3. Preload invokes `hermes:preview:suno-session`.
4. Electron main opens `persist:hermes-preview` and calls
   `cookies.get({ url: 'https://suno.com' })`.
5. Main returns only the exact `__session` cookie, or `null`.

The renderer cannot choose the partition, URL, domain, or cookie name. Electron
reads the cookie jar directly, so the path continues to work if Suno marks the
cookie HttpOnly; it does not use `document.cookie`, the clipboard, or external
CDP.
