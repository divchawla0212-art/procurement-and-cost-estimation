import { useRef, useState } from 'react'
import type { FormEvent, JSX, ReactNode } from 'react'
import { fetchDisciplines, uploadItemVendorList } from '../api'
import { useAsync } from '../useAsync'
import type {
  RfqInput,
  VendorListSource,
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

/**
 * The item's discipline, chosen from the served vocabulary.
 *
 * A picker rather than a text box because the value is matched whole against
 * the client's product group descriptions: free text never matched, so every
 * item read as though no approved vendor covered it.
 *
 * An item whose stored discipline is not in the vocabulary keeps it, as an
 * extra option marked as such. Dropping it would silently rewrite a stored
 * value the moment somebody opened the form to change the quantity — and the
 * items that predate the vocabulary are exactly the ones most likely to be
 * edited.
 */
function DisciplineSelect({
  id,
  value,
  onChange,
  productGroups = false,
}: {
  id: string
  value: string
  onChange: (value: string) => void
  /** Also offer the individual product groups behind each discipline, grouped
   *  under it. An item is scoped to a family; an RFQ is often cut narrower
   *  than that — one cable type rather than all eleven — so its scope line
   *  wants both levels to choose from. */
  productGroups?: boolean
}): JSX.Element {
  const { data } = useAsync(() => fetchDisciplines(), [])
  const families = data ?? []
  const offered = families.flatMap((d) =>
    productGroups ? [d.name, ...d.product_groups] : [d.name],
  )
  // While the fetch is in flight `offered` is empty, so a stored value would
  // briefly look unrecognised. Harmless — it is listed either way, and the
  // label is the only thing that changes when the list lands.
  const unlisted = value !== '' && !offered.includes(value)

  return (
    <select
      id={id}
      className="field"
      required
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">Choose a discipline…</option>
      {productGroups
        ? families.map((d) => (
            <optgroup key={d.name} label={d.name}>
              {/* The family itself first, so "every cable type" stays one
                  click rather than eleven separate RFQs. */}
              <option value={d.name}>
                {d.name} — all {d.product_groups.length} product groups
              </option>
              {d.product_groups.map((group) => (
                <option key={group} value={group}>
                  {group}
                </option>
              ))}
            </optgroup>
          ))
        : families.map((d) => (
            <option key={d.name} value={d.name}>
              {d.name}
            </option>
          ))}
      {unlisted && (
        <option value={value}>{value} — not a listed discipline</option>
      )}
    </select>
  )
}

/**
 * One vendor list upload: the client's, or Astra's.
 *
 * The source is sent by the caller rather than read out of the file, because
 * the Astra list is a subset *of* the client's export and so carries nothing
 * in it that says whose list it is.
 *
 * Reuses the hidden-input pattern the enquiry document uses: the native
 * control's "No file chosen" is browser-owned and cannot be relabelled, so it
 * is hidden behind a button and one status line reports what was read.
 */
function VendorListUpload({
  source,
  projectId,
  itemId,
  onUploaded,
}: {
  source: VendorListSource
  projectId: string
  itemId: string
  onUploaded: () => void
}): JSX.Element {
  const id = `vendor-list-${source.toLowerCase()}`
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [summary, setSummary] = useState<string | null>(null)
  const ref = useRef<HTMLInputElement>(null)

  async function upload(file: File) {
    setBusy(true)
    setError(null)
    setSummary(null)
    try {
      const { summary: s } = await uploadItemVendorList(projectId, itemId, source, file)
      // The counts, not just a tick: an empty list after a 1 346-vendor upload
      // is a narrowing that found nobody, not a failed read, and only the
      // numbers can say which.
      setSummary(`${s.kept} of ${s.parsed} kept · ${s.linked} in the registry`)
      onUploaded()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <span className="fxrow">
      <input
        id={id}
        className="sr-only"
        ref={ref}
        type="file"
        accept=".xlsx"
        aria-label={`Add ${source === 'Client' ? 'client' : 'Astra'} list`}
        disabled={busy}
        onChange={(e) => {
          const file = e.target.files?.[0]
          // Cleared so picking the same file twice reads it twice — the retry
          // after a refused workbook is the case that needs it.
          e.target.value = ''
          if (file) void upload(file)
        }}
      />
      <button type="button" className="btn btn-sm" disabled={busy}
              onClick={() => ref.current?.click()}>
        Add {source === 'Client' ? 'client' : 'Astra'} list
      </button>
      {busy && <span className="muted">Reading…</span>}
      {summary && (
        <span className="muted" role="status">
          <span aria-hidden="true" className="ok-tick">✓</span> {summary}
        </span>
      )}
      {error && <span className="warn">{error}</span>}
    </span>
  )
}

export function ItemForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
  projectId,
  itemId,
  onVendorListUploaded,
}: {
  initial?: WorkflowItemInput
  submitLabel: string
  onSubmit: (body: WorkflowItemInput) => Promise<void>
  onCancel: () => void
  /** Both present only when editing an item that already exists. On the create
   *  form there is no id for an upload to attach to, so the two controls are
   *  absent rather than holding a file that a failed save would strand. */
  projectId?: string
  itemId?: string
  onVendorListUploaded?: () => void
}): JSX.Element {
  const [f, setF] = useState<WorkflowItemInput>(initial ?? BLANK_ITEM)
  // The string the reader is typing is the source of truth for the money
  // field; the number is derived from it once, at submit. Holding only the
  // number is what forced `Number(e.target.value)` on every keystroke, so a
  // half-typed value round-tripped through NaN and back.
  const [valueText, setValueText] = useState(
    String((initial ?? BLANK_ITEM).estimated_value_aed),
  )
  const { onSubmit: submit, error, busy } = useSubmit(async () => {
    const parsed = Number(valueText.trim())
    if (!Number.isFinite(parsed)) throw new Error('Estimated value must be a number.')
    await onSubmit({ ...f, estimated_value_aed: parsed })
  })

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
        <DisciplineSelect
          id="i-disc"
          value={f.discipline}
          onChange={(discipline) => setF({ ...f, discipline })}
        />
      </Row>
      <Row id="i-value" label="Estimated value (AED)">
        {/* Text, not number: a focused number input steps on wheel scroll, so
            scrolling past a filled-in value silently rewrote it. `inputMode`
            keeps the numeric keypad, which is all `type="number"` bought. */}
        <input id="i-value" className="field" type="text" inputMode="decimal"
               required value={valueText}
               onChange={(e) => setValueText(e.target.value)} />
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
      {/* Beside the save controls, and only for an item that already exists.
          The upload posts immediately and is not part of this form's submit —
          it stores against the item on its own, which is why it needs an id
          and why the create form cannot offer it. */}
      {projectId && itemId ? (
        <div className="form-row">
          <VendorListUpload source="Client" projectId={projectId} itemId={itemId}
                            onUploaded={onVendorListUploaded ?? (() => {})} />
          <VendorListUpload source="Astra" projectId={projectId} itemId={itemId}
                            onUploaded={onVendorListUploaded ?? (() => {})} />
        </div>
      ) : (
        <p className="muted">
          Save the item first to add the client and Astra vendor lists.
        </p>
      )}
    </form>
  )
}

/* ----------------------------------------------------------- RaiseRfqForm */

export function RaiseRfqForm({
  projectId,
  itemIds,
  onSubmit,
  onCancel,
  onExtract,
}: {
  projectId: string
  /** The ticked items. The button that opens this form is disabled while this
   *  is empty — an RFQ covering nothing is meaningless, and the server refuses
   *  it anyway. */
  itemIds: string[]
  onSubmit: (body: RfqInput) => Promise<void>
  onCancel: () => void
  /** Reads an enquiry document and hands back whatever of the four fields it
   *  found. Not optional: both screens that raise an RFQ pass it, and a third
   *  that forgot would silently lose the feature rather than fail to build. */
  onExtract: (file: File) => Promise<Partial<RfqInput>>
}): JSX.Element {
  const [reference, setReference] = useState('')
  const [pkg, setPkg] = useState('')
  const [discipline, setDiscipline] = useState('')
  const [value, setValue] = useState('')
  const [extracting, setExtracting] = useState(false)
  const [extractError, setExtractError] = useState<string | null>(null)
  // What the reader picked. The file input clears itself after every read (see
  // below), and its native label was the only thing reporting the choice — so
  // a successful read left the screen saying "No file chosen". This state is
  // that report, and it survives the clear.
  const [picked, setPicked] = useState<File | null>(null)
  // The hidden native input, opened by the visible button below.
  const fileRef = useRef<HTMLInputElement>(null)

  const { onSubmit: submit, error, busy } = useSubmit(async () => {
    // Parsed once, here, rather than on every keystroke: a half-typed value is
    // not an error, and a value that never parses must not reach the server.
    // `Number('1,800,000')` is NaN, which serialises to null and comes back as
    // a validation error naming a field the reader did not think they touched.
    const parsed = Number(value.trim())
    if (!Number.isFinite(parsed)) throw new Error('Estimated budget must be a number.')
    await onSubmit({
      project_id: projectId,
      item_ids: itemIds,
      reference,
      package: pkg,
      discipline,
      value_estimate_aed: parsed,
    })
  })

  /** Fills in what came back and leaves the rest alone. A field the reader has
   *  already typed is overwritten, which is the point — they asked for the
   *  document's version of it — but a field the document did not yield is
   *  never blanked, so a partial answer costs nothing that was already there. */
  async function extract(file: File) {
    // Set before the read, so a failure still leaves the reader able to open
    // the document the failure is about.
    setPicked(file)
    setExtracting(true)
    setExtractError(null)
    try {
      const found = await onExtract(file)
      if (found.reference) setReference(found.reference)
      if (found.package) setPkg(found.package)
      if (found.discipline) setDiscipline(found.discipline)
      if (found.value_estimate_aed) setValue(String(found.value_estimate_aed))
    } catch (err) {
      setExtractError((err as Error).message)
    } finally {
      setExtracting(false)
    }
  }

  return (
    <form className="form-grid" onSubmit={submit}>
      <FormError message={error} />
      <FormError message={extractError} />
      <p className="muted" style={{ margin: 0 }}>
        Covering {itemIds.length} {itemIds.length === 1 ? 'item' : 'items'}.
      </p>
      <Row id="r-doc" label="Fill in from the enquiry document">
        {/* Every field below stays editable afterwards. Nothing is stored by
            the read — the RFQ is created by the submit button, out of whatever
            the reader is looking at by then, so a bad extraction is corrected
            rather than undone. */}
        {/* Hidden, not removed. The browser owns this control's label — it
            reads "No file chosen" and cannot be relabelled from CSS or JS —
            so after a successful read it contradicted the filename beside it.
            It keeps its id, `accept` and label association, so it is still
            what the label names and what a test addresses. */}
        <input
          id="r-doc"
          className="sr-only"
          ref={fileRef}
          type="file"
          accept=".pdf,.docx,.xlsx"
          disabled={extracting}
          onChange={(e) => {
            const file = e.target.files?.[0]
            // Cleared so that picking the same file twice reads it twice —
            // the second attempt after a failure is the case that needs it.
            e.target.value = ''
            if (file) void extract(file)
          }}
        />
        <button
          type="button"
          className="btn btn-sm"
          disabled={extracting}
          onClick={() => fileRef.current?.click()}
        >
          Choose file
        </button>
        {/* The one place this control's state is reported. `role="status"` so
            a change is announced; the tick is decorative and the filename
            carries the meaning. */}
        <span className="muted" role="status">
          {extracting ? (
            'Reading the document…'
          ) : picked ? (
            <>
              <span aria-hidden="true" className="ok-tick">
                ✓
              </span>{' '}
              {picked.name}
            </>
          ) : (
            'Optional. PDF, Word or Excel.'
          )}
        </span>
        {picked && !extracting && (
          <button
            type="button"
            className="linkish"
            // Named, so two forms open at once are distinguishable and a
            // screen reader announces which document is being opened.
            aria-label={`View ${picked.name}`}
            onClick={() => {
              // Created on click rather than on pick: one blob per file the
              // reader tried would leak every one of them. Nothing was
              // uploaded — `/rfqs/extract` stores nothing — so this URL is the
              // only thing that can back the control, and it dies with the
              // form.
              const url = URL.createObjectURL(picked)
              window.open(url, '_blank', 'noopener')
              setTimeout(() => URL.revokeObjectURL(url), 0)
            }}
          >
            View
          </button>
        )}
      </Row>
      <Row id="r-ref" label="Reference">
        <input id="r-ref" className="field" required value={reference}
               onChange={(e) => setReference(e.target.value)} />
      </Row>
      <Row id="r-pkg" label="Package">
        {/* A placeholder rather than help text under the field: it is the one
            field whose *shape* is not obvious from its name, and an example
            says more than a sentence would. It disappears the moment anything
            is typed, so it costs nothing once the habit is formed. */}
        <input id="r-pkg" className="field" required value={pkg}
               placeholder={'Wellhead tie-in ball valves, 11 kV ring main cable…'}
               onChange={(e) => setPkg(e.target.value)} />
      </Row>
      <Row id="r-disc" label="Discipline">
        {/* Product groups offered as well as the families: an RFQ is commonly
            cut narrower than an item's discipline — one cable type, not all
            eleven — and this is the field vendor scope is matched on. */}
        <DisciplineSelect
          id="r-disc"
          value={discipline}
          onChange={setDiscipline}
          productGroups
        />
      </Row>
      <Row id="r-value" label="Estimated budget (AED)">
        {/* Text, not number — see `ItemForm`'s field for why. The two messages
            differ because both forms can be open on one screen, and a shared
            sentence would not say which field to go and fix. */}
        <input id="r-value" className="field" type="text" inputMode="decimal"
               required value={value}
               onChange={(e) => setValue(e.target.value)} />
      </Row>
      <Actions submitLabel="Create RFQ" busy={busy} onCancel={onCancel} />
    </form>
  )
}
