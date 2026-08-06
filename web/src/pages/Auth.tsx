import { useState } from 'react'
import type { FormEvent, JSX } from 'react'
import { MIN_PASSWORD, useAuth } from '../auth/context'
import { Card, PageHeader } from '../components/primitives'

type Mode = 'login' | 'signup'

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
    const result = mode === 'login' ? await login(email, password) : await signup(email, password)
    setSubmitting(false)
    if (!result.ok) {
      setError(result.error ?? 'Something went wrong. Try again.')
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
            <div className="form-row">
              <label htmlFor="auth-password">Password</label>
              <input
                id="auth-password"
                className="input"
                type="password"
                autoComplete={isLogin ? 'current-password' : 'new-password'}
                required
                minLength={isLogin ? undefined : MIN_PASSWORD}
                disabled={submitting}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              {passwordHint && (
                <p className="hint" style={{ color: 'var(--fail)' }}>
                  {passwordHint}
                </p>
              )}
            </div>
            <div className="auth-actions">
              <button type="submit" className="btn btn-primary" disabled={submitting}>
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
