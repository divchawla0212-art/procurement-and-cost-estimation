import type { JSX } from 'react'
import { useNavigate } from 'react-router'

/**
 * The one forward control at the end of a screen.
 *
 * It renders whatever `nav.nextPage` resolved and decides nothing itself —
 * the destination, the reachability rule and the order all live in `NAV`,
 * beside the rail that draws the same table down the side of the page. A
 * component that worked out its own next screen would be a second answer to
 * the question the rail already answers, and the two would diverge on the
 * first insertion.
 *
 * **`null` means no control, never a disabled one.** `nextPage` returns null
 * for a screen that cannot be reached yet, and a large primary button that
 * cannot be pressed is the `Send to 0 vendors` defect in different clothes:
 * it sits where the action goes and its only honest outcome is nothing
 * happening.
 *
 * **The label names the destination.** `Extraction status ›`, not `Next ›` —
 * the button is the largest thing at the bottom of the page, and one that
 * does not say where it goes gives the reader no reason to press it. The
 * chevron is `aria-hidden`, so the accessible name is the destination alone.
 */
export function PageNext({
  next,
}: {
  next: { to: string; label: string } | null
}): JSX.Element | null {
  const navigate = useNavigate()
  if (next === null) return null
  return (
    <div className="page-next">
      <button
        type="button"
        className="btn btn-primary btn-lg"
        onClick={() => navigate(next.to)}
      >
        {next.label} <span aria-hidden="true">›</span>
      </button>
    </div>
  )
}
