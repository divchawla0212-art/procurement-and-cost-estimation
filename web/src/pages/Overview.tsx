import { useState } from 'react'
import type { JSX } from 'react'
import type {
  ComplianceMatrix,
  ProjectDetail,
  Statement,
  StatementRow,
} from '../types'
import {
  fetchComplianceMatrix,
  fetchStatement,
  fetchSummary,
} from '../api'
import { useAsync } from '../useAsync'
import { VERDICTS, VERDICT_LABEL, formatMoney, pct } from '../constants'
import {
  Card,
  ErrorState,
  LoadingState,
  Metric,
  PageHeader,
} from '../components/primitives'
import { Donut, MetricBar, VerdictBar } from '../components/charts'
import type { DonutSegment, VerdictCounts } from '../components/charts'

export interface OverviewProps {
  slug: string
  projectName: string
  onOpenMatrix: (vendor?: string) => void
}

const VERDICT_COLOR: Record<string, string> = {
  pass: 'var(--pass)',
  fail: 'var(--fail)',
  deviation: 'var(--deviation)',
  unanswered: 'var(--unanswered)',
  review: 'var(--review)',
}

interface VendorCompliance {
  vendor: string
  counts: VerdictCounts
  total: number
  pass: number
  fail: number
}

/** Tally each vendor's cells by verdict straight from the matrix. */
function vendorCompliance(matrix: ComplianceMatrix): VendorCompliance[] {
  return matrix.vendors.map((vendor) => {
    const counts: VerdictCounts = {}
    let total = 0
    for (const row of matrix.rows) {
      const cell = row.cells[vendor]
      if (!cell) continue
      counts[cell.verdict] = (counts[cell.verdict] ?? 0) + 1
      total += 1
    }
    return {
      vendor,
      counts,
      total,
      pass: counts['pass'] ?? 0,
      fail: counts['fail'] ?? 0,
    }
  })
}

/** The per-vendor normalized total, read from the statement's award row. */
function normalizedTotals(
  statement: Statement,
): { vendor: string; total: number | null }[] {
  const awardRow: StatementRow | undefined =
    statement.rows.find((r) => r.key === 'normalised') ??
    statement.rows.find((r) => r.key === 'final_value')
  return statement.vendors.map((vendor) => ({
    vendor,
    total: awardRow?.cells[vendor]?.total ?? null,
  }))
}

export function Overview(props: OverviewProps): JSX.Element {
  const { slug, projectName, onOpenMatrix } = props
  const { data, error, loading } = useAsync(
    async () => {
      const [summary, matrix, statement] = await Promise.all([
        fetchSummary(slug),
        fetchComplianceMatrix(slug),
        fetchStatement(slug),
      ])
      return { summary, matrix, statement }
    },
    [slug],
  )

  if (loading) return <LoadingState label="Assembling overview…" />
  if (error) return <ErrorState message={error} />
  if (!data) return <ErrorState message="No overview data was returned." />

  const { summary, matrix, statement } = data
  const hasCompliance = matrix.rows.length > 0 && matrix.vendors.length > 0
  const compliance = hasCompliance ? vendorCompliance(matrix) : []

  const prices = normalizedTotals(statement)
  const pricedVendors = prices.filter((p) => p.total != null)
  const hasPrices = pricedVendors.length > 0

  return (
    <>
      <PageHeader
        eyebrow="Overview"
        title={projectName}
        sub="A read-out of coverage, per-vendor compliance and the priced offers, straight from the store. Click any vendor to open its compliance detail."
      />

      <TopMetrics summary={summary} />

      <div className="charts-grid" style={{ marginTop: '1.4rem' }}>
        <CoverageCard summary={summary} />
        <ComplianceByVendorCard
          compliance={compliance}
          hasCompliance={hasCompliance}
          onOpenMatrix={onOpenMatrix}
        />
      </div>

      <div style={{ marginTop: '1.4rem' }}>
        <PriceCard
          prices={prices}
          hasPrices={hasPrices}
          currency={statement.currency}
        />
      </div>

      <div style={{ marginTop: '1.4rem' }}>
        <RankingCard
          compliance={compliance}
          hasCompliance={hasCompliance}
          prices={prices}
          hasPrices={hasPrices}
          currency={statement.currency}
          onOpenMatrix={onOpenMatrix}
        />
      </div>
    </>
  )
}

/* ------------------------------------------------------------- top metrics */

function TopMetrics({ summary }: { summary: ProjectDetail }): JSX.Element {
  const checked = summary.coverage.auto_cells + summary.coverage.stated_cells
  const passCount = summary.coverage.by_verdict['pass'] ?? 0
  return (
    <div className="metrics">
      <Metric k="Requirements" v={summary.requirement_count} />
      <Metric k="Vendors" v={summary.vendors.length} />
      <Metric k="Checked cells" v={checked} />
      <Metric
        k="Pass rate"
        v={checked ? pct(passCount, checked) : '—'}
        sub={checked ? `${passCount}/${checked}` : undefined}
      />
    </div>
  )
}

/* ----------------------------------------------------------- coverage card */

function CoverageCard({ summary }: { summary: ProjectDetail }): JSX.Element {
  const checked = summary.coverage.auto_cells + summary.coverage.stated_cells
  const segments: DonutSegment[] = VERDICTS.map((v) => ({
    label: VERDICT_LABEL[v],
    value: summary.coverage.by_verdict[v] ?? 0,
    color: VERDICT_COLOR[v],
  })).filter((s) => s.value > 0)

  return (
    <Card title="Coverage">
      {checked === 0 ? (
        <p className="muted mono" style={{ margin: 0 }}>
          No checked cells yet — run ingestion with a real provider to populate
          verdicts.
        </p>
      ) : (
        <div className="donut-row">
          <Donut
            segments={segments}
            centerValue={checked}
            centerLabel="checked"
            ariaLabel="Coverage by verdict"
          />
          <ul className="donut-legend">
            {segments.map((s) => (
              <li key={s.label}>
                <span
                  className="sw"
                  style={{ background: s.color }}
                  aria-hidden
                />
                {s.label}
                <b>{s.value}</b>
                <span className="muted">{pct(s.value, checked)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}

/* ----------------------------------------------- compliance-by-vendor card */

function ComplianceByVendorCard({
  compliance,
  hasCompliance,
  onOpenMatrix,
}: {
  compliance: VendorCompliance[]
  hasCompliance: boolean
  onOpenMatrix: (vendor?: string) => void
}): JSX.Element {
  return (
    <Card title="Compliance by vendor">
      {!hasCompliance ? (
        <p className="muted mono" style={{ margin: 0 }}>
          No compliance data yet — run ingestion with a real provider.
        </p>
      ) : (
        <ul className="vbar-list">
          {compliance.map((c) => (
            <li key={c.vendor}>
              <button
                type="button"
                className="vbar-row"
                onClick={() => onOpenMatrix(c.vendor)}
                title={`Open ${c.vendor} in the compliance matrix`}
              >
                <span className="vbar-name">{c.vendor}</span>
                <VerdictBar counts={c.counts} />
                <span className="vbar-stat mono">
                  {c.total ? pct(c.pass, c.total) : '—'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

/* -------------------------------------------------------------- price card */

function PriceCard({
  prices,
  hasPrices,
  currency,
}: {
  prices: { vendor: string; total: number | null }[]
  hasPrices: boolean
  currency: string
}): JSX.Element {
  const max = Math.max(0, ...prices.map((p) => p.total ?? 0))
  const lowest = hasPrices
    ? prices
        .filter((p) => p.total != null)
        .reduce((a, b) => ((a.total as number) <= (b.total as number) ? a : b))
        .vendor
    : null

  return (
    <Card title={`Normalized price (${currency})`}>
      {!hasPrices ? (
        <p className="muted mono" style={{ margin: 0 }}>
          No priced offers in the statement yet.
        </p>
      ) : (
        <div className="hbars">
          {prices.map((p) => (
            <div className="hbar-row" key={p.vendor}>
              <span className="hbar-name">
                {p.vendor}
                {p.vendor === lowest && (
                  <span className="lead-tag">lowest</span>
                )}
              </span>
              <MetricBar
                value={p.total}
                max={max}
                highlight={p.vendor === lowest}
                valueLabel={
                  p.total != null ? formatMoney(p.total, currency) : '—'
                }
              />
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

/* ------------------------------------------------------------ ranking card */

type SortKey = 'vendor' | 'passRate' | 'fail' | 'price'

interface RankRow {
  vendor: string
  passRate: number | null
  passLabel: string
  fail: number | null
  price: number | null
}

function RankingCard({
  compliance,
  hasCompliance,
  prices,
  hasPrices,
  currency,
  onOpenMatrix,
}: {
  compliance: VendorCompliance[]
  hasCompliance: boolean
  prices: { vendor: string; total: number | null }[]
  hasPrices: boolean
  currency: string
  onOpenMatrix: (vendor?: string) => void
}): JSX.Element {
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({
    key: hasPrices ? 'price' : 'passRate',
    dir: hasPrices ? 1 : -1,
  })

  if (!hasCompliance && !hasPrices) {
    return (
      <Card title="Vendor ranking">
        <p className="muted mono" style={{ margin: 0 }}>
          Nothing to rank yet — run ingestion to compare vendors.
        </p>
      </Card>
    )
  }

  const priceByVendor = new Map(prices.map((p) => [p.vendor, p.total]))
  const complianceByVendor = new Map(compliance.map((c) => [c.vendor, c]))
  const vendors = Array.from(
    new Set([...complianceByVendor.keys(), ...priceByVendor.keys()]),
  )

  const rows: RankRow[] = vendors.map((vendor) => {
    const c = complianceByVendor.get(vendor)
    return {
      vendor,
      passRate: c && c.total ? c.pass / c.total : null,
      passLabel: c && c.total ? `${c.pass}/${c.total}` : '—',
      fail: c ? c.fail : null,
      price: priceByVendor.get(vendor) ?? null,
    }
  })

  // A missing value always sorts last, regardless of direction — a vendor with
  // no price or no verdicts should never masquerade as the leader.
  function cmp(a: number | null, b: number | null, dir: 1 | -1): number {
    if (a == null && b == null) return 0
    if (a == null) return 1
    if (b == null) return -1
    return (a - b) * dir
  }

  const sorted = [...rows].sort((a, b) => {
    if (sort.key === 'vendor') return a.vendor.localeCompare(b.vendor) * sort.dir
    if (sort.key === 'passRate') return cmp(a.passRate, b.passRate, sort.dir)
    if (sort.key === 'fail') return cmp(a.fail, b.fail, sort.dir)
    return cmp(a.price, b.price, sort.dir)
  })

  const bestCompliance = hasCompliance
    ? rows
        .filter((r) => r.passRate != null)
        .reduce<RankRow | null>(
          (best, r) =>
            best == null || (r.passRate as number) > (best.passRate as number)
              ? r
              : best,
          null,
        )?.vendor ?? null
    : null
  const lowestPrice = hasPrices
    ? rows
        .filter((r) => r.price != null)
        .reduce<RankRow | null>(
          (best, r) =>
            best == null || (r.price as number) < (best.price as number)
              ? r
              : best,
          null,
        )?.vendor ?? null
    : null

  function toggle(key: SortKey) {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === 1 ? -1 : 1 }
        : { key, dir: key === 'vendor' ? 1 : key === 'passRate' ? -1 : 1 },
    )
  }

  const arrow = (key: SortKey) =>
    sort.key === key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''

  return (
    <Card title="Vendor ranking">
      <p className="muted" style={{ margin: '0 0 0.8rem', fontSize: '0.83rem' }}>
        Compliance and price are shown side by side, not blended into one score —
        the award is the reviewer's call. Click a header to sort, or a row to
        open that vendor's compliance detail.
      </p>
      <div className="tbl-wrap">
        <table className="grid rank">
          <thead>
            <tr>
              <th
                scope="col"
                className="sortable"
                onClick={() => toggle('vendor')}
              >
                Vendor{arrow('vendor')}
              </th>
              <th
                scope="col"
                className="sortable num"
                onClick={() => toggle('passRate')}
              >
                Pass rate{arrow('passRate')}
              </th>
              <th
                scope="col"
                className="sortable num"
                onClick={() => toggle('fail')}
              >
                Fails{arrow('fail')}
              </th>
              <th
                scope="col"
                className="sortable num"
                onClick={() => toggle('price')}
              >
                Normalized ({currency}){arrow('price')}
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr
                key={r.vendor}
                className="rank-row"
                onClick={() => onOpenMatrix(r.vendor)}
                title={`Open ${r.vendor} in the compliance matrix`}
              >
                <td>
                  <span className="vbar-name">{r.vendor}</span>
                  {r.vendor === bestCompliance && (
                    <span className="lead-tag ok">best compliance</span>
                  )}
                  {r.vendor === lowestPrice && (
                    <span className="lead-tag">lowest price</span>
                  )}
                </td>
                <td className="num">
                  {r.passRate != null ? (
                    <>
                      {Math.round(r.passRate * 100)}%{' '}
                      <span className="muted">{r.passLabel}</span>
                    </>
                  ) : (
                    '—'
                  )}
                </td>
                <td className="num">{r.fail ?? '—'}</td>
                <td className="num">
                  {r.price != null ? formatMoney(r.price, currency) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
