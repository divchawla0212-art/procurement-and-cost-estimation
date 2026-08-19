import type { JSX } from 'react'

export function ShieldMark({
  size = 28,
  tone = 'light',
}: {
  size?: number
  tone?: 'light' | 'dark'
}): JSX.Element {
  const bg = tone === 'light' ? 'rgba(255,255,255,0.10)' : '#1F2B54'
  const stroke = '#FFFFFF'
  return (
    <span
      className="inline-flex items-center justify-center rounded-md"
      style={{ background: bg, width: size, height: size }}
      aria-hidden
    >
      <svg width={size * 0.6} height={size * 0.6} viewBox="0 0 24 24" fill="none">
        <path
          d="M12 2.5 4 6v6c0 4.5 3.3 8.4 8 9.5 4.7-1.1 8-5 8-9.5V6l-8-3.5Z"
          stroke={stroke}
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
        <path
          d="M9 12.2l2.2 2.3L15.5 10"
          stroke={stroke}
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  )
}

export const BRAND_NAME = 'BKS AI Procurement'
