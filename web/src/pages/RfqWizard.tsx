import { useState } from 'react'
import type { JSX } from 'react'
import { fetchRfq, transitionRfq } from '../api'
import { useAsync } from '../useAsync'
import { Card, ErrorState, LoadingState } from '../components/primitives'
import { ClarificationsStep } from './wizard/ClarificationsStep'
import { RaiseRfqStep } from './wizard/RaiseRfqStep'
import { ShortlistingStep } from './wizard/ShortlistingStep'

/**
 * The RFQ, walked one step at a time.
 *
 * Two ideas kept separate on purpose:
 *
 * - **Where the RFQ actually is** — `rfq.stage`, which only ever moves through
 *   a gated transition on the server.
 * - **Which step you are looking at** — local state. You can open any step you
 *   have already passed and keep editing it, because a shortlist that cannot be
 *   corrected after issuance is a shortlist that gets corrected in a spreadsheet
 *   instead.
 *
 * The one thing editing cannot undo is a frozen technical package: the server
 * refuses uploads against one, so the Issued step shows what is in it and says
 * why there is nothing to press. Nothing here freezes a package any more — see
 * `RaiseRfqStep` for what that costs the addendum flow under Clarifications.
 *
 * Ticking a step off is `transitionRfq`. The button is not disabled when the
 * gate is closed — a disabled button explains nothing. It is enabled, and a
 * refusal surfaces the gate's own sentence.
 *
 * Each step lives in `./wizard/`. They were extracted out of this file when the
 * fourth acquired a real editor: four steps in one module is how a screen ends
 * up too large to hold in your head, and every one of them is independently
 * testable now.
 */

/** The steps this phase covers. Bids and evaluation arrive with the ingestion
 *  work, so the wizard stops at Clarifications rather than showing four steps
 *  with nothing behind them. `Scoping` used to open the list; it was removed
 *  from the process, and Issued is now one upload control and the list of what
 *  has been uploaded — `RaiseRfqStep`. */
const WIZARD_STAGES = ['Shortlisting', 'Issued', 'Clarifications'] as const

const STEP_BLURB: Record<string, string> = {
  Shortlisting: 'Record who is invited, and get the list approved.',
  Issued:
    'Upload the documents vendors will bid against, and review the eligibility checklist of what they must return.',
  Clarifications:
    'Bidders ask, you answer, and every answer goes to the whole shortlist unless you record why it does not.',
}

export function RfqWizard({
  rfqId,
  stages,
  onBack,
}: {
  rfqId: string
  stages: string[]
  onBack: () => void
}): JSX.Element {
  const [tick, setTick] = useState(0)
  const { data, error, loading } = useAsync(
    () => fetchRfq(rfqId),
    [rfqId, tick],
    // Every write reloads through `run` below. Without this, each reload
    // blanks `data` for the duration of the refetch, `loading` guard below
    // unmounts the whole step subtree, and any state a step held locally —
    // `SendEnquiry`'s dispatch result among it — dies with it. Same trap as
    // `ItemDetail`'s project read, same fix.
    { keepPreviousData: true },
  )
  const [openStep, setOpenStep] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const reload = () => setTick((t) => t + 1)

  /** Every write goes through here so one place owns the busy flag and the
   *  server's refusal message. */
  async function run(action: () => Promise<unknown>) {
    setBusy(true)
    setActionError(null)
    try {
      await action()
      reload()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  // Only the *first* load blanks the screen. `keepPreviousData` keeps `data`
  // across a reload but `loading` still goes true, so guarding on it alone
  // would unmount the subtree anyway and undo the option above.
  if (loading && !data) return <LoadingState label="Loading RFQ…" />
  if (error) return <ErrorState message={error} />
  if (!data) return <ErrorState message="No RFQ was returned." />

  const { rfq, gate } = data
  const stageIndex = stages.indexOf(rfq.stage)
  const step = openStep ?? (WIZARD_STAGES.includes(rfq.stage as never) ? rfq.stage : WIZARD_STAGES[0])
  const stepIndex = stages.indexOf(step)
  const isCurrentStep = step === rfq.stage
  const nextStage = stages[stageIndex + 1]

  return (
    <>
      <header className="page-head">
        <p className="eyebrow">
          <button type="button" className="linkback" onClick={onBack}>
            ← All RFQs
          </button>
        </p>
        <h1>{rfq.reference}</h1>
        <p className="sub">
          {rfq.package} · {rfq.discipline} · est.{' '}
          {rfq.value_estimate_aed.toLocaleString()} AED
        </p>
        {/* Nothing behind it yet — no search, no provider, no route.
            Disabled rather than enabled-and-inert, and captioned rather than
            bare: this screen's rule is that a control which cannot act says
            why. The stage button below is enabled for the opposite reason —
            there the refusal is the gate's own sentence, which is information
            worth surfacing. Here there is nothing to surface. */}
        <button type="button" className="btn" disabled>
          Search the web for similar vendors
        </button>
        <p className="muted">Not built yet.</p>
      </header>

      <ol className="wizsteps" aria-label="RFQ steps">
        {WIZARD_STAGES.map((name) => {
          const i = stages.indexOf(name)
          const done = i < stageIndex
          const current = name === rfq.stage
          const cls = [
            'wizstep',
            done && 'is-done',
            current && 'is-current',
            name === step && 'is-open',
          ]
            .filter(Boolean)
            .join(' ')
          return (
            <li key={name}>
              <button
                type="button"
                className={cls}
                aria-current={current ? 'step' : undefined}
                onClick={() => setOpenStep(name)}
              >
                <span className="wizstep-mark" aria-hidden="true">
                  {done ? '✓' : i + 1}
                </span>
                <span className="wizstep-name">{name}</span>
              </button>
            </li>
          )
        })}
      </ol>

      {actionError ? <div className="banner banner--error">{actionError}</div> : null}

      {!isCurrentStep ? (
        <div className="banner">
          {stepIndex < stageIndex
            ? `This step is complete. You can still edit it — the RFQ stays at ${rfq.stage}.`
            : `Not reached yet. The RFQ is at ${rfq.stage}.`}
        </div>
      ) : null}

      <Card title={step}>
        <p className="muted">{STEP_BLURB[step]}</p>
        {step === 'Shortlisting' ? (
          <ShortlistingStep data={data} run={run} busy={busy} tick={tick} />
        ) : null}
        {step === 'Issued' ? <RaiseRfqStep data={data} run={run} busy={busy} /> : null}
        {step === 'Clarifications' ? (
          <ClarificationsStep data={data} run={run} busy={busy} tick={tick} />
        ) : null}
      </Card>

      <Card title="Stage history">
        <ol className="historylist">
          {rfq.history.map((entry, i) => (
            // Index is part of the key on purpose: a retender walks the same
            // edge twice, so `to_stage` alone is not unique within one history.
            <li key={`${entry.to_stage}-${i}`}>
              <span className="mono">{entry.at.slice(0, 10)}</span>{' '}
              <b>
                {entry.from_stage ? `${entry.from_stage} → ` : ''}
                {entry.to_stage}
              </b>{' '}
              <span className="muted">by {entry.by}</span>
              {entry.reason ? <div className="muted">{entry.reason}</div> : null}
            </li>
          ))}
        </ol>
      </Card>

      {isCurrentStep && nextStage ? (
        <Card title="Complete this step">
          {/* Only the open gate speaks here. A blocked one used to state its
              reason in this slot; that was removed deliberately, and the
              sentence still reaches the reader — pressing the button below
              fails with the server's 409 and `run` puts those same words in
              the error banner above. The button therefore stays enabled: it is
              now the only thing that can explain the refusal, and disabling it
              would leave a gate nobody can get an answer out of. */}
          {gate.passed ? (
            <p className="muted">Everything {rfq.stage} needs is in place.</p>
          ) : null}
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={() => run(() => transitionRfq(rfqId, nextStage))}
          >
            Mark {rfq.stage} complete → {nextStage}
          </button>
        </Card>
      ) : null}
    </>
  )
}
