import { useState } from 'react'
import type { FormEvent, JSX, ReactNode } from 'react'
import type {
  RfqInput,
  WorkflowItemInput,
  WorkflowProjectInput,
} from '../types'

/**
 * The forms behind the project → item hierarchy.
 *
 * They live here rather than inside the pages that render them because each is
 * used twice — once to create and once to edit — and because `RfqWizard.tsx`
 * already demonstrates the alternative at 600-odd lines: a module that owns its
 * screen, its forms and its state at once is past the size where an edit is
 * reliable.
 *
 * Every one of them is dumb about rules. The server owns what is legal, and a
 * refusal comes back as a sentence these forms show verbatim — never a
 * rewritten "Something went wrong", which would throw away the only part of
 * the answer the reader can act on.
 */

/* --------------------------------------------------------------- plumbing */

/** Runs `submit`, and holds whatever it throws as the form's error. Shared by
 *  all three forms because all three have exactly this lifecycle. */
function useSubmit(submit: () => Promise<void>) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await submit()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return { onSubmit, error, busy }
}

function FormError({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <div className="banner banner--error" role="alert">
      {message}
    </div>
  )
}

function Row({
  id,
  label,
  children,
}: {
  id: string
  label: string
  children: ReactNode
}) {
  return (
    <div className="form-row">
      {/* A real label, not a placeholder: a placeholder disappears on focus
          and is not announced as the field's name. */}
      <label htmlFor={id}>{label}</label>
      {children}
    </div>
  )
}

function Actions({
  submitLabel,
  busy,
  onCancel,
}: {
  submitLabel: string
  busy: boolean
  onCancel: () => void
}) {
  return (
    <div style={{ display: 'flex', gap: '0.5rem' }}>
      <button type="submit" className="btn btn-primary" disabled={busy}>
        {submitLabel}
      </button>
      <button type="button" className="btn btn-ghost" onClick={onCancel}>
        Cancel
      </button>
    </div>
  )
}

/* ----------------------------------------------------------- ProjectForm */

const BLANK_PROJECT: WorkflowProjectInput = {
  name: '',
  code: '',
  client: '',
  location: '',
  live_period_start: '',
  live_period_end: '',
  currency: 'AED',
}

export function ProjectForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  /** Present when editing. Absent means create, and the form starts blank. */
  initial?: WorkflowProjectInput
  submitLabel: string
  onSubmit: (body: WorkflowProjectInput) => Promise<void>
  onCancel: () => void
}): JSX.Element {
  const [f, setF] = useState<WorkflowProjectInput>(initial ?? BLANK_PROJECT)
  const { onSubmit: submit, error, busy } = useSubmit(() => onSubmit(f))

  const set = (k: keyof WorkflowProjectInput) => (v: string) =>
    setF((prev) => ({ ...prev, [k]: v }))

  return (
    <form className="form-grid" onSubmit={submit}>
      <FormError message={error} />
      <Row id="p-name" label="Name">
        <input id="p-name" className="field" required value={f.name}
               onChange={(e) => set('name')(e.target.value)} />
      </Row>
      <Row id="p-code" label="Code">
        <input id="p-code" className="field" required value={f.code}
               onChange={(e) => set('code')(e.target.value)} />
      </Row>
      <Row id="p-client" label="Client">
        <input id="p-client" className="field" required value={f.client}
               onChange={(e) => set('client')(e.target.value)} />
      </Row>
      <Row id="p-location" label="Location">
        <input id="p-location" className="field" required value={f.location}
               onChange={(e) => set('location')(e.target.value)} />
      </Row>
      <Row id="p-start" label="Live period start">
        <input id="p-start" className="field" type="date" required
               value={f.live_period_start}
               onChange={(e) => set('live_period_start')(e.target.value)} />
      </Row>
      <Row id="p-end" label="Live period end">
        <input id="p-end" className="field" type="date" required
               value={f.live_period_end}
               onChange={(e) => set('live_period_end')(e.target.value)} />
      </Row>
      <Row id="p-currency" label="Currency">
        <input id="p-currency" className="field" required value={f.currency}
               onChange={(e) => set('currency')(e.target.value)} />
      </Row>
      {/* `status` is offered only when editing. A project is always created
          Active, and there is nothing to choose at that moment. */}
      {initial && (
        <Row id="p-status" label="Status">
          <select id="p-status" className="field" value={f.status ?? 'Active'}
                  onChange={(e) =>
                    setF((prev) => ({
                      ...prev,
                      status: e.target.value as WorkflowProjectInput['status'],
                    }))
                  }>
            <option value="Active">Active</option>
            <option value="On Hold">On Hold</option>
            <option value="Closed">Closed</option>
          </select>
        </Row>
      )}
      <Actions submitLabel={submitLabel} busy={busy} onCancel={onCancel} />
    </form>
  )
}

/* -------------------------------------------------------------- ItemForm */

const BLANK_ITEM: WorkflowItemInput = {
  item_type: '',
  description: '',
  qty: 1,
  uom: '',
  discipline: '',
  estimated_value_aed: 0,
  required_on_site: null,
  is_long_lead: false,
}

export function ItemForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial?: WorkflowItemInput
  submitLabel: string
  onSubmit: (body: WorkflowItemInput) => Promise<void>
  onCancel: () => void
}): JSX.Element {
  const [f, setF] = useState<WorkflowItemInput>(initial ?? BLANK_ITEM)
  const { onSubmit: submit, error, busy } = useSubmit(() => onSubmit(f))

  return (
    <form className="form-grid" onSubmit={submit}>
      <FormError message={error} />
      <Row id="i-type" label="Item type">
        <input id="i-type" className="field" required value={f.item_type}
               onChange={(e) => setF({ ...f, item_type: e.target.value })} />
      </Row>
      <Row id="i-desc" label="Description">
        <input id="i-desc" className="field" required value={f.description}
               onChange={(e) => setF({ ...f, description: e.target.value })} />
      </Row>
      <Row id="i-qty" label="Quantity">
        <input id="i-qty" className="field" type="number" step="any" required
               value={f.qty}
               onChange={(e) => setF({ ...f, qty: Number(e.target.value) })} />
      </Row>
      <Row id="i-uom" label="Unit of measure">
        <input id="i-uom" className="field" required value={f.uom}
               onChange={(e) => setF({ ...f, uom: e.target.value })} />
      </Row>
      <Row id="i-disc" label="Discipline">
        <input id="i-disc" className="field" required value={f.discipline}
               onChange={(e) => setF({ ...f, discipline: e.target.value })} />
      </Row>
      <Row id="i-value" label="Estimated value (AED)">
        <input id="i-value" className="field" type="number" required
               value={f.estimated_value_aed}
               onChange={(e) =>
                 setF({ ...f, estimated_value_aed: Number(e.target.value) })
               } />
      </Row>
      <Row id="i-date" label="Required on site">
        {/* Optional, and an empty date field means "not decided yet" — sent as
            null rather than as an empty string, which is not a date. */}
        <input id="i-date" className="field" type="date"
               value={f.required_on_site ?? ''}
               onChange={(e) =>
                 setF({ ...f, required_on_site: e.target.value || null })
               } />
      </Row>
      <div className="form-row">
        <label htmlFor="i-longlead">
          <input id="i-longlead" type="checkbox" checked={f.is_long_lead}
                 onChange={(e) => setF({ ...f, is_long_lead: e.target.checked })} />
          {' '}Long lead
        </label>
      </div>
      <Actions submitLabel={submitLabel} busy={busy} onCancel={onCancel} />
    </form>
  )
}

/* ----------------------------------------------------------- RaiseRfqForm */

export function RaiseRfqForm({
  projectId,
  itemIds,
  onSubmit,
  onCancel,
}: {
  projectId: string
  /** The ticked items. The button that opens this form is disabled while this
   *  is empty — an RFQ covering nothing is meaningless, and the server refuses
   *  it anyway. */
  itemIds: string[]
  onSubmit: (body: RfqInput) => Promise<void>
  onCancel: () => void
}): JSX.Element {
  const [reference, setReference] = useState('')
  const [pkg, setPkg] = useState('')
  const [discipline, setDiscipline] = useState('')
  const [value, setValue] = useState('')

  const { onSubmit: submit, error, busy } = useSubmit(() =>
    onSubmit({
      project_id: projectId,
      item_ids: itemIds,
      reference,
      package: pkg,
      discipline,
      value_estimate_aed: Number(value),
    }),
  )

  return (
    <form className="form-grid" onSubmit={submit}>
      <FormError message={error} />
      <p className="muted" style={{ margin: 0 }}>
        Covering {itemIds.length} {itemIds.length === 1 ? 'item' : 'items'}.
      </p>
      <Row id="r-ref" label="Reference">
        <input id="r-ref" className="field" required value={reference}
               onChange={(e) => setReference(e.target.value)} />
      </Row>
      <Row id="r-pkg" label="Package">
        <input id="r-pkg" className="field" required value={pkg}
               onChange={(e) => setPkg(e.target.value)} />
      </Row>
      <Row id="r-disc" label="Discipline">
        <input id="r-disc" className="field" required value={discipline}
               onChange={(e) => setDiscipline(e.target.value)} />
      </Row>
      <Row id="r-value" label="Value estimate (AED)">
        <input id="r-value" className="field" type="number" required value={value}
               onChange={(e) => setValue(e.target.value)} />
      </Row>
      <Actions submitLabel="Create RFQ" busy={busy} onCancel={onCancel} />
    </form>
  )
}
