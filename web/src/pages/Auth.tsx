import { useState } from 'react'
import type { FormEvent, JSX } from 'react'
import { MIN_PASSWORD, useAuth } from '../auth/context'
import { Card, PageHeader, PasswordField } from '../components/primitives'

type Mode = 'login' | 'signup'

// A typing convenience for the demo administrator, and only that: clicking it
// fills the email field and you still supply the password. Nothing verifies
// this address exists — a store seeded without it shows a chip whose sign-in
// then fails on the server's own "Invalid email or password", which is the
// same answer any wrong address gets.
const ADMIN_ACCOUNT = 'admin@gmail.com'

// Shipped in the bundle, so this password is public to anyone who loads the
// page — treat the demo administrator as a published account, and never reuse
// this address or password for anything that matters. Signing in is still a
// second, deliberate press of the button; the chip only fills the fields.
const ADMIN_PASSWORD = 'Admin@1234'

export function Auth(): JSX.Element {
  const { login, signup } = useAuth()
  const [mode, setMode] = useState<Mode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [passwordHint, setPasswordHint] = useState<string | null>(null)

  function switchMode(next: Mode) {
    setMode(next)
    setError(null)
    setPasswordHint(null)
  }

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setError(null)
    setPasswordHint(null)

    if (mode === 'signup' && password.length < MIN_PASSWORD) {
      setPasswordHint(`Use at least ${MIN_PASSWORD} characters.`)
      return
    }

    setSubmitting(true)
    try {
      const result = mode === 'login' ? await login(email, password) : await signup(email, password)
      if (!result.ok) {
        setError(result.error ?? 'Something went wrong. Try again.')
      }
    } finally {
      // A `finally` here — not a plain statement after the await — is the
      // point: it re-enables the button even if login/signup ever throws
      // instead of resolving to a failed AuthResult, so no future error path
      // can strand the form disabled.
      setSubmitting(false)
    }
  }

  const isLogin = mode === 'login'

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <PageHeader
          eyebrow="Tender Eval"
          title={isLogin ? 'Sign in' : 'Create an account'}
          sub={
            isLogin
              ? 'Sign in to review procurement projects.'
              : 'New accounts start as a reviewer with no projects granted yet — an admin grants access separately.'
          }
        />
        <Card>
          <form className="form-grid" onSubmit={onSubmit}>
            {error && <div className="banner banner--error">{error}</div>}
            <div className="form-row">
              <label htmlFor="auth-email">Email</label>
              <input
                id="auth-email"
                className="input"
                type="email"
                autoComplete="email"
                required
                disabled={submitting}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            {isLogin && (
              <div className="form-row">
                <span className="hint">Admin account</span>
                <div>
                  <button
                    type="button"
                    className="chip chip--asis"
                    disabled={submitting}
                    onClick={() => {
                      setEmail(ADMIN_ACCOUNT)
                      setPassword(ADMIN_PASSWORD)
                      // Both fields are filled, so the next act is the button
                      // rather than the password box. By id rather than a ref
                      // because the submit lives inside this form's own markup
                      // and one id is cheaper than threading a ref through it.
                      document.getElementById('auth-submit')?.focus()
                    }}
                  >
                    {ADMIN_ACCOUNT}
                  </button>
                </div>
              </div>
            )}
            <div className="form-row">
              <label htmlFor="auth-password">Password</label>
              <PasswordField
                id="auth-password"
                autoComplete={isLogin ? 'current-password' : 'new-password'}
                required
                minLength={isLogin ? undefined : MIN_PASSWORD}
                disabled={submitting}
                value={password}
                onChange={setPassword}
              />
              {passwordHint && (
                <p className="hint" style={{ color: 'var(--fail)' }}>
                  {passwordHint}
                </p>
              )}
            </div>
            <div className="auth-actions">
              <button id="auth-submit" type="submit" className="btn btn-primary" disabled={submitting}>
                {submitting
                  ? isLogin
                    ? 'Signing in…'
                    : 'Creating account…'
                  : isLogin
                    ? 'Sign in'
                    : 'Create account'}
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={submitting}
                onClick={() => switchMode(isLogin ? 'signup' : 'login')}
              >
                {isLogin ? 'Need an account? Sign up' : 'Have an account? Sign in'}
              </button>
            </div>
          </form>
        </Card>
      </div>
    </div>
  )
}
