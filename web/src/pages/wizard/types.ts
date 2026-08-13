import type { RfqDetail } from '../../types'

/** What every wizard step is handed.
 *
 *  `run` owns the busy flag and the server's refusal message, so no step
 *  re-implements either — one place shows the gate's own sentence. */
export type StepProps = {
  data: RfqDetail
  run: (action: () => Promise<unknown>) => Promise<void>
  busy: boolean
  /** The wizard's reload counter. Only steps that load a *second* resource
   *  need it: that resource changes when a write here succeeds, and without
   *  the tick it would go stale. */
  tick: number
}
