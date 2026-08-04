// The reachability rule for screens `02 Compliance matrix` and
// `03 Comparative statement`. Pulled out of App.tsx (see BUG-001 in
// BUGS_TRACKER.md) so the rule can be unit-tested independently of whether
// anything actually wires it into a `disabled` prop.
//
// Decision (design spec §1.1): `done` and `done_with_failures` both admit —
// a project with one unreadable document among many still has a usable
// matrix, and `04 Extraction status` already reports which documents
// failed. `new` (no run yet) and `failed` (a run that extracted nothing) do
// not, because the store holds no extraction to read. An unrecognised
// status — a value from a newer API this client does not know about yet —
// is treated as not reachable: the safe direction is the one that shows the
// honest screen rather than an empty-looking matrix.
const REVIEWABLE = new Set(['done', 'done_with_failures'])

export function reviewReachable(status: string | null | undefined): boolean {
  return status != null && REVIEWABLE.has(status)
}
