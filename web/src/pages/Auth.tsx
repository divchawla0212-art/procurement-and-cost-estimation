import { useState } from 'react'
import type { FormEvent, JSX } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { ArrowRight, ShieldCheck, Lock } from 'lucide-react'
import { MIN_PASSWORD, useAuth } from '../auth/context'
import { ShieldMark, BRAND_NAME } from '../components/ShieldMark'

const ADMIN_ACCOUNT = 'admin@gmail.com'
const ADMIN_PASSWORD = 'Admin@1234'

const DEMO_TILES = [
  {
    id: 'business-admin',
    title: 'Business Admin',
    desc: 'Full edit access to all projects, reports & invoices',
    email: ADMIN_ACCOUNT,
    password: ADMIN_PASSWORD,
    color: '#3B82F6',
  },
  {
    id: 'project-lead',
    title: 'Project Lead',
    desc: 'Manages owned projects, ingestion & contracts',
    email: 'lead@bks.ai',
    password: '',
    color: '#10B981',
  },
  {
    id: 'vendor-lead',
    title: 'Vendor Lead',
    desc: 'Uploads & submits proposal documents',
    email: 'vendor@kerui.com',
    password: '',
    color: '#F59E0B',
  },
  {
    id: 'tech-admin',
    title: 'Technology Admin',
    desc: 'Read-only system & data maintenance access',
    email: 'tech@bks.ai',
    password: '',
    color: '#64748B',
  },
] as const

export function Auth(): JSX.Element {
  const { login, signup } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const returnTo =
    location.pathname !== '/login' && location.pathname !== '/'
      ? location.pathname + location.search
      : '/projects'

  const [mode, setMode] = useState<'login' | 'signup'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [passwordHint, setPasswordHint] = useState<string | null>(null)

  function fillDemo(tile: (typeof DEMO_TILES)[number]) {
    setEmail(tile.email)
    setPassword(tile.password)
    setError(null)
    document.getElementById('auth-submit')?.focus()
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
      if (result.ok) {
        navigate(returnTo, { replace: true })
      } else {
        setError(result.error ?? 'Something went wrong. Try again.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  const isLogin = mode === 'login'

  return (
    <div className="bks-public min-h-screen w-full grid grid-cols-1 lg:grid-cols-2">
      <div className="login-mesh relative text-white overflow-hidden hidden lg:flex flex-col justify-between px-12 xl:px-14 py-10">
        <Link to="/" className="flex items-center gap-2.5">
          <ShieldMark size={30} />
          <span className="text-[15px] font-semibold tracking-tight">{BRAND_NAME}</span>
        </Link>

        <div className="max-w-[480px] fade-up">
          <h1 className="display-heading text-white text-4xl xl:text-[2.75rem]">
            Procurement &amp; cost estimation, governed end to end.
          </h1>
          <p className="mt-5 text-[15px] leading-relaxed text-white/65 max-w-[420px]">
            Role-based control across projects, vendor proposals, comparison reports and contracts
            — with a full audit trail on every action.
          </p>
        </div>

        <div className="trust-row !text-white/55">
          <span className="inline-flex items-center gap-2">
            <ShieldCheck size={14} /> SOC 2 aligned
          </span>
          <span aria-hidden>·</span>
          <span>Role-based access</span>
          <span aria-hidden>·</span>
          <span>Full audit trail</span>
        </div>
      </div>

      <div className="flex items-center justify-center px-6 sm:px-10 py-14 bg-white">
        <div className="w-full max-w-[400px] fade-up">
          <div className="lg:hidden mb-8 flex items-center gap-2.5">
            <ShieldMark size={26} tone="dark" />
            <span className="text-[15px] font-semibold tracking-tight">{BRAND_NAME}</span>
          </div>

          <h2 className="text-[1.75rem] font-bold tracking-tight text-[var(--ink)]">
            {isLogin ? 'Sign in' : 'Create an account'}
          </h2>
          <p className="mt-1.5 text-[14px] text-[var(--muted)]">
            {isLogin
              ? 'Access your procurement workspace.'
              : 'New accounts start as a reviewer — an admin grants project access separately.'}
          </p>

          <form onSubmit={onSubmit} className="mt-7 space-y-4">
            <div>
              <label htmlFor="auth-email" className="field-label">
                Email
              </label>
              <input
                id="auth-email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@bks.ai"
                disabled={submitting}
                className="public-input mt-1.5"
              />
            </div>

            <div>
              <label htmlFor="auth-password" className="field-label">
                Password
              </label>
              <input
                id="auth-password"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                disabled={submitting}
                minLength={isLogin ? undefined : MIN_PASSWORD}
                autoComplete={isLogin ? 'current-password' : 'new-password'}
                className="public-input mt-1.5"
              />
              {passwordHint && (
                <p className="mt-2 text-[13px] text-red-600">{passwordHint}</p>
              )}
            </div>

            {error && (
              <div className="text-[13px] text-red-600 bg-red-50 border border-red-100 rounded-[var(--radius)] px-3 py-2.5 flex items-center gap-2">
                <Lock size={13} /> {error}
              </div>
            )}

            <button
              id="auth-submit"
              type="submit"
              disabled={submitting}
              className="btn-navy w-full h-11 flex items-center justify-center gap-2 disabled:opacity-70 mt-1"
            >
              {submitting ? (
                isLogin ? 'Signing in…' : 'Creating account…'
              ) : (
                <>
                  {isLogin ? 'Sign in' : 'Create account'} <ArrowRight size={15} />
                </>
              )}
            </button>
          </form>

          {isLogin && (
            <div className="mt-8">
              <div className="eyebrow">Demo Accounts</div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                {DEMO_TILES.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    disabled={submitting}
                    onClick={() => fillDemo(t)}
                    className="demo-tile"
                  >
                    <div className="flex items-center gap-2">
                      <span className="role-dot" style={{ background: t.color }} />
                      <span className="text-[12.5px] font-semibold text-[var(--ink)]">
                        {t.title}
                      </span>
                    </div>
                    <div className="mt-1 text-[11.5px] text-[var(--muted)] leading-snug">
                      {t.desc}
                    </div>
                  </button>
                ))}
              </div>
              <p className="mt-2.5 text-[11px] text-[var(--muted-2)]">
                Click a tile to fill demo credentials.
              </p>
            </div>
          )}

          <p className="mt-6 text-[13px] text-center text-[var(--muted)]">
            {isLogin ? (
              <>
                Need an account?{' '}
                <button
                  type="button"
                  className="font-semibold text-[var(--navy-800)] hover:underline underline-offset-2"
                  disabled={submitting}
                  onClick={() => setMode('signup')}
                >
                  Sign up
                </button>
              </>
            ) : (
              <>
                Have an account?{' '}
                <button
                  type="button"
                  className="font-semibold text-[var(--navy-800)] hover:underline underline-offset-2"
                  disabled={submitting}
                  onClick={() => setMode('login')}
                >
                  Sign in
                </button>
              </>
            )}
          </p>
        </div>
      </div>
    </div>
  )
}
