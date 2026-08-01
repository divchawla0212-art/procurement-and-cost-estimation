import { useEffect, useMemo, useState } from 'react'
import { fetchComplianceMatrix } from '../api'
import type { ComplianceMatrix, GroupKey, Verdict } from '../types'
import { rowHasVerdict, rowMatchesQuery } from '../matrixUtils'
import { CoverageStrip } from './CoverageStrip'
import { MatrixFilters } from './MatrixFilters'
import { WorklistView } from './WorklistView'
import { GridView } from './GridView'

interface Props {
  slug: string
  projectName: string
}

function filterRows(
  rows: MatrixRowLike[],
  query: string,
  verdicts: Set<string>,
  vendorFilter: string | null,
) {
  return rows.filter(
    (row) =>
      rowMatchesQuery(row, query) && rowHasVerdict(row, verdicts, vendorFilter),
  )
}

type MatrixRowLike = ComplianceMatrix['rows'][number]

export function MatrixView({ slug, projectName }: Props) {
  const [matrix, setMatrix] = useState<ComplianceMatrix | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [mode, setMode] = useState<'worklist' | 'grid'>('worklist')
  const [query, setQuery] = useState('')
  const [selectedVerdicts, setSelectedVerdicts] = useState<Set<string>>(
    () => new Set(),
  )
  const [vendorFilter, setVendorFilter] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setMatrix(null)
    fetchComplianceMatrix(slug)
      .then((data) => {
        if (!cancelled) setMatrix(data)
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [slug])

  const toggleVerdict = (v: Verdict) => {
    setSelectedVerdicts((prev) => {
      const next = new Set(prev)
      if (next.has(v)) next.delete(v)
      else next.add(v)
      return next
    })
  }

  const filtered = useMemo(() => {
    if (!matrix) return null
    const rows = filterRows(
      matrix.rows,
      query,
      selectedVerdicts,
      vendorFilter,
    )
    const groups = {
      not_matched: filterRows(
        matrix.groups.not_matched ?? [],
        query,
        selectedVerdicts,
        vendorFilter,
      ),
      needs_human: filterRows(
        matrix.groups.needs_human ?? [],
        query,
        selectedVerdicts,
        vendorFilter,
      ),
      matched: filterRows(
        matrix.groups.matched ?? [],
        query,
        selectedVerdicts,
        vendorFilter,
      ),
    } satisfies Record<GroupKey, MatrixRowLike[]>
    return { rows, groups }
  }, [matrix, query, selectedVerdicts, vendorFilter])

  if (loading) {
    return <div className="panel loading">Loading compliance matrix…</div>
  }
  if (error) {
    return (
      <div className="panel error">
        <h2>Could not load matrix</h2>
        <p>{error}</p>
      </div>
    )
  }
  if (!matrix || !filtered) return null

  if (!matrix.rows.length) {
    return (
      <div className="panel empty">
        <h1>{projectName}</h1>
        <p className="lead">
          Run ingestion in the Streamlit portal to build the compliance matrix.
        </p>
        <p className="muted">
          Create or open the project there, upload requirements and vendor
          folders, then click <strong>Run ingestion</strong>. This screen is
          read-only.
        </p>
      </div>
    )
  }

  if (!matrix.vendors.length) {
    return (
      <div className="panel empty">
        <h1>{projectName}</h1>
        <p className="lead">
          The project has no vendors, so there is nothing to compare.
        </p>
      </div>
    )
  }

  return (
    <div className="matrix-workspace">
      <header className="workspace-header">
        <div>
          <p className="eyebrow">Compliance matrix</p>
          <h1>{projectName}</h1>
        </div>
        <p className="muted">
          {matrix.rows.length} requirements · {matrix.vendors.length} vendors
        </p>
      </header>

      <CoverageStrip coverage={matrix.coverage} />

      <MatrixFilters
        query={query}
        onQueryChange={setQuery}
        selectedVerdicts={selectedVerdicts}
        onToggleVerdict={toggleVerdict}
        vendors={matrix.vendors}
        vendorFilter={vendorFilter}
        onVendorFilter={setVendorFilter}
        mode={mode}
        onModeChange={setMode}
      />

      {mode === 'worklist' ? (
        <WorklistView groups={filtered.groups} vendors={matrix.vendors} />
      ) : (
        <GridView rows={filtered.rows} vendors={matrix.vendors} />
      )}
    </div>
  )
}
