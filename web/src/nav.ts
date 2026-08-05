// The reachability rule for screens `02 Compliance matrix` and
// `03 Comparative statement`. Pulled out of App.tsx (see BUG-001 in
// BUGS_TRACKER.md) so the rule can be unit-tested independently of whether
// anything actually wires it into a `disabled` prop.
//
// Decision (design spec §1.1, corrected — see the correction note in that
// section): this predicate originally consulted `status`, on the theory that
// `new` and `failed` were the two states where the store holds no extraction
// to read. That was wrong for `failed`: CLAUDE.md's store invariant "a
// failed extraction never blanks previously-good stored data" means a
// project whose first run succeeded and whose *second* run failed outright
// (an expired key, a provider outage, or a forced re-run) still holds the
// first run's complete extraction, with `status` recomputed to `"failed"`
// per document (`procurement/pipeline.py`: `"failed" if extracted == 0`).
//
// The right predicate is "does the store hold results to read", and
// `has_results` (`ProjectSummary.has_results`, computed in
// `api/main.py::_project_summary` the same way `_setup_state` already does —
// `bool(load_dataset(ROOT, slug))`) answers exactly that. A project with
// results is reviewable whatever its `status`; a project without is not.
// `status` now drives only the partial/failed-run banner on the review
// screens, not this gate.
export function reviewReachable(hasResults: boolean | null | undefined): boolean {
  return hasResults === true
}
