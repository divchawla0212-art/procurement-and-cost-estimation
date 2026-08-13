import { createContext, useContext, useEffect, useRef, useState } from 'react'
import type { JSX, ReactNode } from 'react'
import { useLocation, useNavigate, useNavigationType } from 'react-router'
import { EMPTY, canGoBack, canGoForward, visit } from './history-stack'

/**
 * Back and forward, and whether either is available.
 *
 * The availability question is the whole reason this exists. The History API
 * cannot answer it — see the comment at the top of `history-stack.ts` — so the
 * model is ours, fed from the two things React Router reports on every
 * navigation. This component owns the feeding; `history-stack.ts` owns the rule.
 */
interface NavHistoryValue {
  canBack: boolean
  canForward: boolean
}

const NavHistoryContext = createContext<NavHistoryValue>({
  canBack: false,
  canForward: false,
})

export function useNavHistory(): NavHistoryValue {
  return useContext(NavHistoryContext)
}

export function NavHistoryProvider({ children }: { children: ReactNode }): JSX.Element {
  const location = useLocation()
  const navigationType = useNavigationType()
  const [model, setModel] = useState(EMPTY)

  // The key of the entry already folded in. `visit` is deliberately not
  // idempotent — a second PUSH of the same key appends it twice — and StrictMode
  // invokes effects twice in development, so without this guard every
  // navigation would be counted once in production and twice in a dev build.
  // React Router mints a fresh key per navigation, including for a REPLACE onto
  // the same path, so comparing keys never swallows a real one.
  const folded = useRef<string | null>(null)

  useEffect(() => {
    if (folded.current === location.key) return
    folded.current = location.key
    setModel((m) => visit(m, location.key, navigationType))
  }, [location.key, navigationType])

  return (
    <NavHistoryContext.Provider
      value={{ canBack: canGoBack(model), canForward: canGoForward(model) }}
    >
      {children}
    </NavHistoryContext.Provider>
  )
}

/**
 * The ← → pair in the status bar.
 *
 * Disabled rather than hidden: a control that vanishes moves everything beside
 * it, and at the far left of a status bar that means the whole row twitches on
 * the first navigation of every session.
 *
 * These do not replace the per-page `← Back to projects` buttons or the
 * breadcrumbs. Those mean "up to my parent", which on a screen reached from the
 * rail is a different place from "back to where I was".
 */
export function BackForwardControls(): JSX.Element {
  const { canBack, canForward } = useNavHistory()
  const navigate = useNavigate()

  return (
    <div className="sb-nav">
      <button
        type="button"
        className="sb-nav-btn"
        aria-label="Go back"
        title="Go back"
        disabled={!canBack}
        onClick={() => navigate(-1)}
      >
        ←
      </button>
      <button
        type="button"
        className="sb-nav-btn"
        aria-label="Go forward"
        title="Go forward"
        disabled={!canForward}
        onClick={() => navigate(1)}
      >
        →
      </button>
    </div>
  )
}
