import { createContext, useContext } from 'react'

export interface User {
  id: string
  email: string
  role: 'admin' | 'reviewer'
}

export interface AuthResult {
  ok: boolean
  error?: string
}

export interface AuthContextValue {
  user: User | null
  // False until the initial GET /api/auth/me settles (success or failure).
  // Without this, the first render has `user === null` and the shell would
  // flash the login screen at an already-signed-in user before the session
  // check returns.
  ready: boolean
  login: (email: string, password: string) => Promise<AuthResult>
  signup: (email: string, password: string) => Promise<AuthResult>
  logout: () => Promise<void>
}

// Matches the server's Credentials.password minimum (api/auth/routes.py). A
// client minimum below the server's would turn a helpful inline message into
// a 422 the form has to render as a generic server error instead.
export const MIN_PASSWORD = 8

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be called within an AuthProvider')
  }
  return ctx
}
