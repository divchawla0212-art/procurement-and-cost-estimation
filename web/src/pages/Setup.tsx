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
import { useAuth } from '../auth/context'
import {
  Card,
  EmptyState,
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
  const { user } = useAuth()
  // Every mutation this screen offers — create, both uploads, FX, ingest — is
  // `require_admin` on the server (api/main.py). A reviewer's `GET .../setup`
  // is not, so the screen still has something true to show them: what has been
  // attached, which vendors are loaded, whether ingestion has run. So the
  // screen stays and the controls go, rather than hiding the whole thing.
  // This is presentation only; the server refuses these calls either way.
  const canEdit = user?.role === 'admin'

  if (props.slug === null) {
    if (!canEdit) {
      return (
        <>
          <PageHeader
            eyebrow="Setup"
            title="No project selected"
            sub="Reviewers see the projects an administrator has granted them."
          />
          <EmptyState title="Nothing to set up here">
            Only an administrator creates projects and runs ingestion. If you
            expected to see a tender, ask them to grant you access to it.
          </EmptyState>
        </>
      )
    }
    return <CreateProject onCreated={props.onCreated} reload={props.reload} />
  }
  return (
    <ConfigureProject
      slug={props.slug}
      onNew={props.onNew}
      reload={props.reload}
      onOpen={props.onOpen}
      canEdit={canEdit}
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
  canEdit,
}: {
  slug: string
  onNew: () => void
  reload: () => void
  onOpen: (slug: string) => void
  canEdit: boolean
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
        sub={
          canEdit
            ? 'Attach the requirement, upload vendor bids, set any FX rates, then run ingestion.'
            : 'How this tender was set up. An administrator makes these changes.'
        }
        actions={
          canEdit ? (
            <button className="btn btn-ghost" onClick={onNew}>
              New project
            </button>
          ) : undefined
        }
      />
      <Card>
        <div className="stepper">
          <StepFrame
            n={1}
            done={done[0]}
            active={activeIndex === 0}
            title="Requirements document"
            note={
              canEdit
                ? "The buyer's spec — PDF, DOCX, or XLSX. Its clauses become the compliance rows."
                : "The buyer's spec. Its clauses are the compliance rows you review."
            }
          >
            <RequirementsStep
              slug={slug}
              setup={setup}
              onDone={refresh}
              canEdit={canEdit}
            />
          </StepFrame>

          <StepFrame
            n={2}
            done={done[1]}
            active={activeIndex === 1}
            title="Vendor bids"
            note={
              canEdit
                ? 'Upload a ZIP whose top-level folders are vendors. Add more any time — each upload adds to the roster.'
                : 'The vendors whose bids were uploaded for this tender.'
            }
          >
            <VendorsStep
              slug={slug}
              setup={setup}
              onDone={refresh}
              canEdit={canEdit}
            />
          </StepFrame>

          <StepFrame
            n={3}
            done={done[2]}
            active={activeIndex === 2}
            title="FX rates"
            note={
              canEdit
                ? `Enter a rate to ${setup.target_currency} for any other currency a vendor quoted in. Leave empty if every offer is already in ${setup.target_currency}.`
                : `The rates used to normalise offers quoted in another currency to ${setup.target_currency}.`
            }
          >
            <FxStep
              slug={slug}
              setup={setup}
              onSaved={refresh}
              canEdit={canEdit}
            />
          </StepFrame>

          <StepFrame
            n={4}
            done={done[3]}
            active={activeIndex === 3}
            title="Run ingestion"
            note={
              canEdit
                ? 'Extract every vendor bid and build the comparison. This can take a few minutes with a live model.'
                : 'Whether the bids have been extracted and the comparison built.'
            }
          >
            <IngestStep
              slug={slug}
              setup={setup}
              onDone={refresh}
              onOpen={onOpen}
              canEdit={canEdit}
            />
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
  canEdit,
}: {
  slug: string
  setup: ProjectSetup
  onDone: () => void
  canEdit: boolean
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
          {canEdit && (
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => inputRef.current?.click()}
            >
              Replace
            </button>
          )}
        </div>
      )}
      {canEdit ? (
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
      ) : (
        !setup.requirements_file && (
          <p className="hint">No requirements document attached yet.</p>
        )
      )}
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
  canEdit,
}: {
  slug: string
  setup: ProjectSetup
  onDone: () => void
  canEdit: boolean
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
      {canEdit ? (
        <Dropzone
          accept=".zip"
          title="Drop a vendor ZIP or browse"
          hint="One ZIP; top-level folders are vendors"
          busy={busy}
          onFile={handle}
        />
      ) : (
        setup.vendors.length === 0 && (
          <p className="hint">No vendor bids uploaded yet.</p>
        )
      )}
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
  canEdit,
}: {
  canEdit: boolean
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

  if (!canEdit) {
    return rows.length ? (
      <>
        {rows.map((row) => (
          <div className="filerow" key={row.code}>
            <span className="fname">{row.code}</span>
            <span className="fmeta">
              1 {row.code} = {row.rate} {setup.target_currency}
            </span>
          </div>
        ))}
      </>
    ) : (
      <p className="hint">
        No FX rates set — every offer is treated as already quoted in{' '}
        {setup.target_currency}.
      </p>
    )
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

function ProviderBanner({ option }: { option: ProviderOption }): JSX.Element {
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
  canEdit,
}: {
  slug: string
  setup: ProjectSetup
  onDone: () => void
  onOpen: (slug: string) => void
  canEdit: boolean
}): JSX.Element {
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [provider, setProvider] = useState(setup.provider.provider)

  // A cached bundle talking to an older API during a rolling deploy would get
  // no `catalog` at all; falling back to [] keeps step 4 rendering instead of
  // white-screening on `.find`.
  const catalog = setup.provider.catalog ?? []
  const inCatalog = catalog.some((entry) => entry.id === provider)
  const selected =
    catalog.find((entry) => entry.id === provider) ??
    { id: provider, needs_key: setup.provider.needs_key, ready: setup.provider.ready }

  const anthropic = catalog.find((entry) => entry.id === 'anthropic')
  const scannedPdfsUnsupported =
    selected.id !== 'anthropic' && anthropic !== undefined && !anthropic.ready

  const noVendors = setup.vendors.length === 0
  const canRun = !noVendors && selected.ready && !running

  async function run() {
    setRunning(true)
    setError(null)
    try {
      await runIngestion(slug, provider)
      onDone()
    } catch (err) {
      setError((err as Error).message)
      setRunning(false)
    }
  }

  if (!canEdit) {
    // No provider banner: which model runs the extraction is an operator's
    // concern, and a reviewer can do nothing about a missing key. What they
    // need is whether results exist and a way into them.
    return (
      <>
        {setup.has_results ? (
          <>
            <div className="banner banner--ok">Ingestion complete.</div>
            <div style={{ marginTop: '0.6rem' }}>
              <button className="btn btn-ink" onClick={() => onOpen(slug)}>
                Review compliance matrix
              </button>
            </div>
          </>
        ) : (
          <p className="hint">
            Ingestion has not run yet, so there are no results to review.
          </p>
        )}
      </>
    )
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
              and it would read as broken. Show what it is actually set to. */}
          {!inCatalog && (
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
