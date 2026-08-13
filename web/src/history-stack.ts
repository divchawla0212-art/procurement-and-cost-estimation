// Whether Back and Forward are available — a question neither the browser nor
// React Router can answer, so we keep the model ourselves.
//
// `window.history.length` is the obvious wrong instinct. It counts entries from
// before this application loaded, it never shrinks, and there is no
// `canGoForward` on the History API at all — by design, since knowing what a
// user visited before arriving here would leak their browsing history. React
// Router does not expose it either.
//
// So this module tracks it, from the two things React Router *does* report on
// every navigation: `useLocation().key` and `useNavigationType()`. It is pure —
// no router, no DOM, no hooks — so the rule is unit-testable on its own, the way
// `nav.ts` is.

export type NavType = 'PUSH' | 'POP' | 'REPLACE'

/**
 * The entries we know about and where we are among them.
 *
 * The invariant: `keys` holds **exactly** what is reachable from `index` —
 * `index` entries behind, `keys.length - index - 1` ahead, and nothing that a
 * PUSH truncated.
 */
export type HistoryModel = {
  keys: string[]
  index: number
}

/** Before the first navigation is seen. `index` is -1 because there is no
 *  current entry yet, not because we are before the first one. */
export const EMPTY: HistoryModel = { keys: [], index: -1 }

export function visit(model: HistoryModel, key: string, type: NavType): HistoryModel {
  // The first navigation of a session arrives as a POP (React Router reports the
  // initial render that way), so the empty model has to absorb any type as a
  // seed rather than treating it as a step from nothing.
  if (model.keys.length === 0) return { keys: [key], index: 0 }

  if (type === 'REPLACE') {
    // Overwrite in place: neither length nor position moves. This is what keeps
    // a guard redirect out of the history, so Back skips past the URL that
    // redirected instead of landing on it and being thrown forward again.
    const keys = model.keys.slice()
    keys[model.index] = key
    return { keys, index: model.index }
  }

  if (type === 'POP') {
    // Resolved by lookup, not by stepping one position: the browser's own
    // history menu can jump several entries at once, and one branch covers
    // back, forward and the jump.
    const found = model.keys.indexOf(key)
    if (found !== -1) return { keys: model.keys, index: found }

    // A key we have never seen. The entry predates this application session —
    // arrived at by Back from the page the user was on before us, say — and we
    // genuinely cannot know what sits on either side of it. Reset to just this
    // entry, which reports "no" in both directions. Saying "no" to an unknown
    // is honest; guessing produces a button that does nothing when pressed.
    return { keys: [key], index: 0 }
  }

  // PUSH. Truncating first is the whole point: going back and then navigating
  // somewhere new abandons the old forward branch, and an implementation that
  // only appends would leave Forward enabled pointing into a branch that is no
  // longer reachable.
  const keys = [...model.keys.slice(0, model.index + 1), key]
  return { keys, index: keys.length - 1 }
}

export function canGoBack(model: HistoryModel): boolean {
  return model.index > 0
}

export function canGoForward(model: HistoryModel): boolean {
  return model.index >= 0 && model.index < model.keys.length - 1
}
