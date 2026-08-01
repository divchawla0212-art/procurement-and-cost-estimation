import { VERDICTS } from '../matrixUtils'
import type { Verdict } from '../types'

interface Props {
  query: string
  onQueryChange: (q: string) => void
  selectedVerdicts: Set<string>
  onToggleVerdict: (v: Verdict) => void
  vendors: string[]
  vendorFilter: string | null
  onVendorFilter: (v: string | null) => void
  mode: 'worklist' | 'grid'
  onModeChange: (m: 'worklist' | 'grid') => void
}

export function MatrixFilters({
  query,
  onQueryChange,
  selectedVerdicts,
  onToggleVerdict,
  vendors,
  vendorFilter,
  onVendorFilter,
  mode,
  onModeChange,
}: Props) {
  return (
    <div className="filters">
      <div className="mode-toggle" role="group" aria-label="View mode">
        <button
          type="button"
          className={mode === 'worklist' ? 'active' : ''}
          onClick={() => onModeChange('worklist')}
        >
          Worklist
        </button>
        <button
          type="button"
          className={mode === 'grid' ? 'active' : ''}
          onClick={() => onModeChange('grid')}
        >
          Full grid
        </button>
      </div>

      <label className="search">
        <span className="sr-only">Search</span>
        <input
          type="search"
          placeholder="Search clause, requirement, rationale…"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
        />
      </label>

      <div className="verdict-chips" role="group" aria-label="Filter by verdict">
        {VERDICTS.map((v) => (
          <button
            key={v}
            type="button"
            className={`chip verdict-${v} ${selectedVerdicts.has(v) ? 'selected' : ''}`}
            onClick={() => onToggleVerdict(v)}
          >
            {v}
          </button>
        ))}
      </div>

      {vendors.length > 0 && (
        <label className="vendor-filter">
          Vendor
          <select
            value={vendorFilter ?? ''}
            onChange={(e) => onVendorFilter(e.target.value || null)}
          >
            <option value="">All vendors</option>
            {vendors.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  )
}
