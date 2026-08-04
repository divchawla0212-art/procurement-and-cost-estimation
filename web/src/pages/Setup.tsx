import { useEffect, useRef, useState } from 'react'
import type { FormEvent, JSX, ReactNode, RefObject } from 'react'
import type { ProjectSetup, ProviderOption, VendorSetup } from '../types'
import {
  createProject,
  fetchSetup,
  runIngestion,
  saveFxRates,
  uploadRequirements,
  uploadVendors,
} from '../api'
import { useAsync } from '../useAsync'
import {
  Card,
  ErrorState,
  LoadingState,
  PageHeader,
} from '../components/primitives'

export interface SetupProps {
  slug: string | null
  onCreated: (slug: string) => void
  onNew: () => void
  reload: () => void
  onOpen: (slug: string) => void
}

export function Setup(props: SetupProps): JSX.Element {
  if (props.slug === null) {
    return <CreateProject onCreated={props.onCreated} reload={props.reload} />
  }
  return (
    <ConfigureProject
      slug={props.slug}
      onNew={props.onNew}
      reload={props.reload}
      onOpen={props.onOpen}
    />
  )
}

/* ------------------------------------------------------------- create mode */

function CreateProject({
  onCreated,
  reload,
}: {
  onCreated: (slug: string) => void
  reload: () => void
}): JSX.Element {
  const [name, setName] = useState('')
  const [currency, setCurrency] = useState('USD')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nameError, setNameError] = useState<string | null>(null)

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) {
      setNameError('Enter a project name.')
      return
    }
    setNameError(null)
    setError(null)
    setSubmitting(true)
    try {
      const result = await createProject(trimmed, currency.trim() || 'USD')
      reload()
      onCreated(result.slug)
    } catch (err) {
      setError((err as Error).message)
      setSubmitting(false)
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Setup"
        title="New project"
        sub="Name the tender and pick the currency you want every offer normalised to."
      />
      <Card>
        <form className="form-grid" onSubmit={onSubmit}>
          {error && <div className="banner banner--error">{error}</div>}
          <div className="form-row">
            <label htmlFor="setup-name">Project name</label>
            <input
              id="setup-name"
              className="input"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            {nameError && (
              <p className="hint" style={{ color: 'var(--fail)' }}>
                {nameError}
              </p>
            )}
          </div>
          <div className="form-row">
            <label htmlFor="setup-currency">Target currency</label>
            <input
              id="setup-currency"
              className="input"
              maxLength={3}
              value={currency}
              onChange={(e) => setCurrency(e.target.value.toUpperCase())}
            />
          </div>
          <div>
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? 'Creating…' : 'Create project'}
            </button>
          </div>
        </form>
      </Card>
    </>
  )
}

/* ---------------------------------------------------------- configure mode */

function ConfigureProject({
  slug,
  onNew,
  reload,
  onOpen,
}: {
  slug: string
  onNew: () => void
  reload: () => void
  onOpen: (slug: string) => void
}): JSX.Element {
  const [tick, setTick] = useState(0)
  const { data: setup, error, loading } = useAsync(
    () => fetchSetup(slug),
    [slug, tick],
  )

  const refresh = () => {
    setTick((t) => t + 1)
    reload()
  }

  if (loading) return <LoadingState label="Loading project…" />
  if (error) return <ErrorState message={error} />
  if (!setup) return <ErrorState message="No project data was returned." />

  const done = [
    Boolean(setup.requirements_file),
    setup.vendors.length > 0,
    Object.keys(setup.fx_rates).length > 0,
    setup.has_results,
  ]
  const activeIndex = done.findIndex((d) => !d)

  return (
    <>
      <PageHeader
        eyebrow="Setup"
        title={setup.name}
        sub="Attach the requirement, upload vendor bids, set any FX rates, then run ingestion."
        actions={
          <button className="btn btn-ghost" onClick={onNew}>
            New project
          </button>
        }
      />
      <Card>
        <div className="stepper">
          <StepFrame
            n={1}
            done={done[0]}
            active={activeIndex === 0}
            title="Requirements document"
            note="The buyer's spec — PDF, DOCX, or XLSX. Its clauses become the compliance rows."
          >
            <RequirementsStep slug={slug} setup={setup} onDone={refresh} />
          </StepFrame>

          <StepFrame
            n={2}
            done={done[1]}
            active={activeIndex === 1}
            title="Vendor bids"
            note="Upload a ZIP whose top-level folders are vendors. Add more any time — each upload adds to the roster."
          >
            <VendorsStep slug={slug} setup={setup} onDone={refresh} />
          </StepFrame>

          <StepFrame
            n={3}
            done={done[2]}
            active={activeIndex === 2}
            title="FX rates"
            note={`Enter a rate to ${setup.target_currency} for any other currency a vendor quoted in. Leave empty if every offer is already in ${setup.target_currency}.`}
          >
            <FxStep slug={slug} setup={setup} onSaved={refresh} />
          </StepFrame>

          <StepFrame
            n={4}
            done={done[3]}
            active={activeIndex === 3}
            title="Run ingestion"
            note="Extract every vendor bid and build the comparison. This can take a few minutes with a live model."
          >
            <IngestStep slug={slug} setup={setup} onDone={refresh} onOpen={onOpen} />
          </StepFrame>
        </div>
      </Card>
    </>
  )
}

/* --------------------------------------------------------------- step frame */

function StepFrame({
  n,
  done,
  active,
  title,
  note,
  children,
}: {
  n: number
  done: boolean
  active: boolean
  title: string
  note: string
  children: ReactNode
}): JSX.Element {
  const cls = `step${done ? ' done' : ''}${active ? ' active' : ''}`
  return (
    <div className={cls}>
      <div className="step-marker">{done ? '✓' : n}</div>
      <div className="step-body">
        <h3 className="step-title">{title}</h3>
        <p className="step-note">{note}</p>
        {children}
      </div>
    </div>
  )
}

/* ----------------------------------------------------------------- dropzone */

function Dropzone({
  accept,
  title,
  hint,
  busy,
  onFile,
  inputRef,
}: {
  accept: string
  title: string
  hint: string
  busy: boolean
  onFile: (file: File) => void
  inputRef?: RefObject<HTMLInputElement | null>
}): JSX.Element {
  const [drag, setDrag] = useState(false)
  return (
    <label
      className={`dropzone${drag ? ' drag' : ''}`}
      onDragOver={(e) => {
        e.preventDefault()
        setDrag(true)
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDrag(false)
        const file = e.dataTransfer.files[0]
        if (file) onFile(file)
      }}
    >
      <input
        ref={inputRef}
        type="file"
        className="sr-only"
        accept={accept}
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) onFile(file)
          e.target.value = ''
        }}
      />
      <span className="dz-glyph" aria-hidden>
        ↥
      </span>
      <span className="dz-title">{title}</span>
      <span className="hint">{busy ? 'Uploading…' : hint}</span>
    </label>
  )
}

/* ----------------------------------------------------- step 1: requirements */

function RequirementsStep({
  slug,
  setup,
  onDone,
}: {
  slug: string
  setup: ProjectSetup
  onDone: () => void
}): JSX.Element {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  async function handle(file: File) {
    setBusy(true)
    setError(null)
    try {
      await uploadRequirements(slug, file)
      onDone()
    } catch (err) {
      setError((err as Error).message)
      setBusy(false)
    }
  }

  return (
    <>
      {setup.requirements_file && (
        <div className="filerow">
          <span className="fname">{setup.requirements_file}</span>
          <span className="fmeta">attached</span>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => inputRef.current?.click()}
          >
            Replace
          </button>
        </div>
      )}
      <div style={{ marginTop: setup.requirements_file ? '0.6rem' : 0 }}>
        <Dropzone
          accept=".pdf,.docx,.xlsx"
          title="Drop the requirements file or browse"
          hint="PDF, DOCX or XLSX"
          busy={busy}
          onFile={handle}
          inputRef={inputRef}
        />
      </div>
      {error && (
        <div className="banner banner--error" style={{ marginTop: '0.6rem' }}>
          {error}
        </div>
      )}
    </>
  )
}

/* --------------------------------------------------------- step 2: vendors */

function VendorRow({ vendor }: { vendor: VendorSetup }): JSX.Element {
  return (
    <div className="filerow">
      <span className="fname">{vendor.name}</span>
      <span className="fmeta">
        {vendor.file_count} files · quote: {vendor.quote ?? '—'}
      </span>
    </div>
  )
}

function VendorsStep({
  slug,
  setup,
  onDone,
}: {
  slug: string
  setup: ProjectSetup
  onDone: () => void
}): JSX.Element {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handle(file: File) {
    setBusy(true)
    setError(null)
    try {
      await uploadVendors(slug, file)
      onDone()
    } catch (err) {
      setError((err as Error).message)
      setBusy(false)
    }
  }

  return (
    <>
      {setup.vendors.length > 0 && (
        <div style={{ marginBottom: '0.6rem' }}>
          {setup.vendors.map((v) => (
            <VendorRow key={v.name} vendor={v} />
          ))}
        </div>
      )}
      <Dropzone
        accept=".zip"
        title="Drop a vendor ZIP or browse"
        hint="One ZIP; top-level folders are vendors"
        busy={busy}
        onFile={handle}
      />
      {error && (
        <div className="banner banner--error" style={{ marginTop: '0.6rem' }}>
          {error}
        </div>
      )}
    </>
  )
}

/* ------------------------------------------------------------ step 3: fx */

interface FxRow {
  code: string
  rate: string
}

function FxStep({
  slug,
  setup,
  onSaved,
}: {
  slug: string
  setup: ProjectSetup
  onSaved: () => void
}): JSX.Element {
  const [rows, setRows] = useState<FxRow[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [ok, setOk] = useState(false)

  useEffect(() => {
    const entries = Object.entries(setup.fx_rates)
    setRows(entries.map(([code, rate]) => ({ code, rate: String(rate) })))
    setError(null)
  }, [setup])

  function edit(index: number, patch: Partial<FxRow>) {
    setRows((rs) => rs.map((r, i) => (i === index ? { ...r, ...patch } : r)))
    setOk(false)
  }

  function addRow() {
    setRows((rs) => [...rs, { code: '', rate: '' }])
    setOk(false)
  }

  function removeRow(index: number) {
    setRows((rs) => rs.filter((_, i) => i !== index))
    setOk(false)
  }

  async function save() {
    setSaving(true)
    setError(null)
    setOk(false)
    const rates: Record<string, number> = {}
    for (const row of rows) {
      const code = row.code.trim().toUpperCase()
      const rate = Number(row.rate)
      if (code && row.rate.trim() !== '' && Number.isFinite(rate)) {
        rates[code] = rate
      }
    }
    try {
      await saveFxRates(slug, rates)
      setOk(true)
      onSaved()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      {rows.map((row, i) => (
        <div className="fxrow" key={i} style={{ marginBottom: '0.5rem' }}>
          <input
            className="input"
            maxLength={3}
            aria-label="Currency code"
            value={row.code}
            onChange={(e) => edit(i, { code: e.target.value.toUpperCase() })}
          />
          <input
            className="input"
            type="number"
            inputMode="decimal"
            step="0.0001"
            aria-label="Rate"
            value={row.rate}
            onChange={(e) => edit(i, { rate: e.target.value })}
          />
          <button
            className="btn btn-ghost btn-sm"
            aria-label="Remove rate"
            onClick={() => removeRow(i)}
          >
            Remove
          </button>
        </div>
      ))}
      <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.4rem', flexWrap: 'wrap' }}>
        <button className="btn btn-sm" onClick={addRow}>
          Add rate
        </button>
        <button className="btn btn-primary" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save FX rates'}
        </button>
      </div>
      {ok && (
        <div className="banner banner--ok" style={{ marginTop: '0.6rem' }}>
          FX rates saved.
        </div>
      )}
      {error && (
        <div className="banner banner--error" style={{ marginTop: '0.6rem' }}>
          {error}
        </div>
      )}
    </>
  )
}

/* ------------------------------------------------------- step 4: ingestion */

function ProviderBanner({
  option,
}: {
  option: ProviderOption | null
}): JSX.Element {
  if (option === null) {
    return (
      <div className="banner banner--warn">
        No provider configured. Choose one below.
      </div>
    )
  }
  if (option.ready) {
    return (
      <div className="banner banner--ok">
        Extraction provider: {option.id} — ready.
      </div>
    )
  }
  return (
    <div className="banner banner--warn">
      Extraction provider "{option.id}" is not ready.
      {option.needs_key
        ? ` Set ${option.needs_key} in the API environment and restart it.`
        : ''}
    </div>
  )
}

function IngestStep({
  slug,
  setup,
  onDone,
  onOpen,
}: {
  slug: string
  setup: ProjectSetup
  onDone: () => void
  onOpen: (slug: string) => void
}): JSX.Element {
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // '' is the "nothing selected yet" sentinel for the controlled <select>: a
  // native select's value can't be null. It only arises when the server has
  // no default (BUG-004: LLM_PROVIDER unset) and the reviewer hasn't picked
  // one either.
  const [provider, setProvider] = useState(setup.provider.provider ?? '')

  // A cached bundle talking to an older API during a rolling deploy would get
  // no `catalog` at all; falling back to [] keeps step 4 rendering instead of
  // white-screening on `.find`.
  const catalog = setup.provider.catalog ?? []
  const inCatalog = catalog.some((entry) => entry.id === provider)
  const selected: ProviderOption | null =
    catalog.find((entry) => entry.id === provider) ??
    (provider === ''
      ? null
      : { id: provider, needs_key: setup.provider.needs_key, ready: setup.provider.ready })

  const anthropic = catalog.find((entry) => entry.id === 'anthropic')
  const scannedPdfsUnsupported =
    selected !== null && selected.id !== 'anthropic' &&
    anthropic !== undefined && !anthropic.ready

  const noVendors = setup.vendors.length === 0
  const canRun = !noVendors && selected !== null && selected.ready && !running

  async function run() {
    setRunning(true)
    setError(null)
    try {
      await runIngestion(slug, provider || undefined)
      onDone()
    } catch (err) {
      setError((err as Error).message)
      setRunning(false)
    }
  }

  // Guard 3 (design spec §1.2): a forced run re-spends the full LLM cost of
  // the project, so it confirms before firing and names the cost. Guard 2 —
  // a separate, secondary action shown only once there are results to force
  // over — is enforced at the call site below (`setup.has_results`).
  async function runForced() {
    const ok = window.confirm(
      'This re-extracts every vendor document from scratch, ignoring the ' +
      'cache, and re-spends the full LLM cost of the project. Continue?',
    )
    if (!ok) return
    setRunning(true)
    setError(null)
    try {
      await runIngestion(slug, provider || undefined, true)
      onDone()
    } catch (err) {
      setError((err as Error).message)
      setRunning(false)
    }
  }

  return (
    <>
      <ProviderBanner option={selected} />
      {running && (
        <div className="banner" style={{ marginTop: '0.6rem' }}>
          Extracting and comparing vendor bids…
        </div>
      )}
      {setup.has_results && !running && (
        <div className="banner banner--ok" style={{ marginTop: '0.6rem' }}>
          Ingestion complete.
        </div>
      )}
      {error && (
        <div className="banner banner--error" style={{ marginTop: '0.6rem' }}>
          {error}
        </div>
      )}
      <div className="form-row" style={{ marginTop: '0.6rem', maxWidth: 320 }}>
        <label htmlFor="provider-select">Extraction provider</label>
        <select
          id="provider-select"
          className="input"
          value={provider}
          disabled={running}
          onChange={(e) => setProvider(e.target.value)}
        >
          {/* A selection outside the catalog — an off-catalog LLM_PROVIDER —
              matches no option, so the browser would render the control blank
              and it would read as broken. Show what it is actually set to.
              '' is the separate "nothing configured, nothing chosen" case
              (BUG-004): the placeholder names that state honestly instead of
              rendering an empty label. */}
          {!inCatalog && provider === '' && (
            <option value="" disabled>
              No provider configured
            </option>
          )}
          {!inCatalog && provider !== '' && (
            <option value={provider} disabled>
              {provider} — not a known provider
            </option>
          )}
          {catalog.map((entry) => (
            <option key={entry.id} value={entry.id} disabled={!entry.ready}>
              {entry.ready
                ? entry.id
                : `${entry.id} — ${entry.needs_key ?? 'not configured'} not set`}
            </option>
          ))}
        </select>
        <p className="hint">
          Applies to this run only. Reloading returns to the server default.
        </p>
        {scannedPdfsUnsupported && (
          <p className="hint">
            No ANTHROPIC_API_KEY is set, so scanned image-only PDFs will not be
            transcribed on this run.
          </p>
        )}
      </div>
      <div
        style={{
          display: 'flex',
          gap: '0.5rem',
          alignItems: 'center',
          flexWrap: 'wrap',
          marginTop: '0.6rem',
        }}
      >
        <button
          className="btn btn-primary"
          disabled={!canRun}
          onClick={run}
        >
          {running ? 'Running ingestion…' : 'Run ingestion'}
        </button>
        {/* BUG-002: a distinct, secondary action from `Run ingestion`, shown
            only once there are results to force over (guard 2) and confirmed
            before it fires (guard 3) - see runForced above. Before the first
            run there is nothing cached to bypass. */}
        {setup.has_results && (
          <button
            className="btn btn-ghost"
            disabled={!canRun}
            onClick={runForced}
            title="Re-extracts every document from scratch, ignoring the cache."
          >
            {running ? 'Running ingestion…' : 'Force full re-extraction'}
          </button>
        )}
        {setup.has_results && (
          <button className="btn btn-ink" onClick={() => onOpen(slug)}>
            Review compliance matrix
          </button>
        )}
      </div>
      {noVendors && (
        <p className="hint" style={{ marginTop: '0.4rem' }}>
          Add at least one vendor first.
        </p>
      )}
    </>
  )
}
