import type { JSX } from 'react'
import { VERDICTS, VERDICT_LABEL, pct } from '../constants'

/* ============================================================================
   Dependency-free charts.
   Deliberately no charting library: these three shapes (a ring, a stacked bar,
   a horizontal bar) are small enough that hand-rolled SVG/CSS keeps the bundle
   lean and the styling on the same verdict palette as the rest of the app.
   ========================================================================== */

export interface DonutSegment {
  label: string
  value: number
  /** A CSS color — a `var(--pass)` token or any valid colour string. */
  color: string
}

/**
 * A single-value ring. Segments are laid end to end proportional to their
 * value; a `<title>` on each makes the exact count discoverable on hover.
 */
export function Donut({
  segments,
  size = 172,
  thickness = 22,
  centerValue,
  centerLabel,
  ariaLabel = 'Distribution',
}: {
  segments: DonutSegment[]
  size?: number
  thickness?: number
  centerValue?: string | number
  centerLabel?: string
  ariaLabel?: string
}): JSX.Element {
  const total = segments.reduce((sum, s) => sum + s.value, 0)
  const r = (size - thickness) / 2
  const circ = 2 * Math.PI * r
  let offset = 0

  return (
    <svg
      className="donut"
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={ariaLabel}
    >
      <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--surface-2)"
          strokeWidth={thickness}
        />
        {total > 0 &&
          segments
            .filter((s) => s.value > 0)
            .map((s) => {
              const len = (s.value / total) * circ
              const dash = -offset
              offset += len
              return (
                <circle
                  key={s.label}
                  cx={size / 2}
                  cy={size / 2}
                  r={r}
                  fill="none"
                  stroke={s.color}
                  strokeWidth={thickness}
                  strokeDasharray={`${len} ${circ - len}`}
                  strokeDashoffset={dash}
                >
                  <title>{`${s.label}: ${s.value} (${pct(s.value, total)})`}</title>
                </circle>
              )
            })}
      </g>
      {centerValue !== undefined && (
        <text
          className="donut-value"
          x="50%"
          y="50%"
          textAnchor="middle"
          dominantBaseline="middle"
          dy={centerLabel ? '-0.15em' : '0'}
        >
          {centerValue}
        </text>
      )}
      {centerLabel && (
        <text
          className="donut-label"
          x="50%"
          y="50%"
          textAnchor="middle"
          dominantBaseline="middle"
          dy="1.2em"
        >
          {centerLabel}
        </text>
      )}
    </svg>
  )
}

export type VerdictCounts = Record<string, number>

/**
 * A horizontal stacked bar of verdict counts, reusing the coverage
 * instrument's segment palette so it reads identically across the app.
 */
export function VerdictBar({ counts }: { counts: VerdictCounts }): JSX.Element {
  const total = VERDICTS.reduce((sum, v) => sum + (counts[v] ?? 0), 0)
  if (!total) {
    return (
      <div className="cov-bar" aria-hidden>
        <div className="cov-seg" style={{ width: '100%', opacity: 0.4 }} />
      </div>
    )
  }
  return (
    <div className="cov-bar" role="img" aria-label="Verdicts">
      {VERDICTS.map((v) => {
        const count = counts[v] ?? 0
        if (!count) return null
        return (
          <div
            key={v}
            className={`cov-seg ${v}`}
            style={{ width: `${(count / total) * 100}%` }}
            title={`${VERDICT_LABEL[v]}: ${count} (${pct(count, total)})`}
          />
        )
      })}
    </div>
  )
}

/**
 * A labelled horizontal bar, scaled against `max`. Used for the normalized
 * price comparison; `highlight` shades the leading (lowest) offer.
 */
export function MetricBar({
  value,
  max,
  valueLabel,
  highlight = false,
}: {
  value: number | null
  max: number
  valueLabel: string
  highlight?: boolean
}): JSX.Element {
  const width = value != null && max > 0 ? Math.max((value / max) * 100, 2) : 0
  return (
    <div className="hbar">
      <div className="hbar-track">
        <div
          className={`hbar-fill${highlight ? ' lead' : ''}`}
          style={{ width: `${width}%` }}
        />
      </div>
      <span className="hbar-value mono">{valueLabel}</span>
    </div>
  )
}
