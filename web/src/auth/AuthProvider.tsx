import { useEffect, useState } from 'react'
import type { JSX, ReactNode } from 'react'
import type { AuthResult, User } from './context'
import { AuthContext } from './context'

// The mock provider this replaces (PR #7) wrote every account it created —
// including the plaintext password — into localStorage under these keys, so
// this browser upgrade clears them rather than leaving them sitting there.
const LEGACY_KEYS = ['te_users', 'te_session']

/** Read a FastAPI error body's `detail` string, falling back to the status line. */
async function readDetail(res: Response): Promise<string> {
  try {
    const body = await res.json()
    if (typeof body?.detail === 'string') return body.detail
  } catch {
    /* non-JSON error body; fall through to the status line */
  }
  return `${res.status} ${res.statusText}`
}

async function postCredentials(
  path: string,
  email: string,
  password: string,
): Promise<{ user: User | null; error?: string }> {
  let res: Response
  try {
    res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
  } catch {
    // fetch itself rejected — offline, connection refused, backend down —
    // rather than the server answering with an error status. Resolve to a
    // normal failed AuthResult so the caller's await never throws.
    return { user: null, error: 'Could not reach the server. Check your connection and try again.' }
  }
  if (!res.ok) {
    return { user: null, error: await readDetail(res) }
  }
  return { user: (await res.json()) as User }
}

export function AuthProvider({ children }: { children: ReactNode }): JSX.Element {
  const [user, setUser] = useState<User | null>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    LEGACY_KEYS.forEach((k) => localStorage.removeItem(k))

    // The session lives in an HttpOnly cookie, so it cannot be inspected from
    // JS — restoring it on load means asking the server, not reading storage.
    fetch('/api/auth/me')
      .then((res) => (res.ok ? (res.json() as Promise<User>) : null))
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setReady(true))
  }, [])

  async function login(email: string, password: string): Promise<AuthResult> {
    const result = await postCredentials('/api/auth/login', email, password)
    if (!result.user) return { ok: false, error: result.error }
    setUser(result.user)
    return { ok: true }
  }

  async function signup(email: string, password: string): Promise<AuthResult> {
    const result = await postCredentials('/api/auth/signup', email, password)
    if (!result.user) return { ok: false, error: result.error }
    setUser(result.user)
    return { ok: true }
  }

  async function logout(): Promise<void> {
    try {
      await fetch('/api/auth/logout', { method: 'POST' })
    } finally {
      setUser(null)
    }
  }

  return (
    <AuthContext.Provider value={{ user, ready, login, signup, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
