import type { JSX } from 'react'
import { Link } from 'react-router'
import {
  ArrowRight,
  ShieldCheck,
  Sparkles,
  FileText,
  GitBranch,
  Lock,
  LineChart,
} from 'lucide-react'
import { ShieldMark, BRAND_NAME } from '../components/ShieldMark'

const CLIENTS = ['ADNOC', 'TAQA', 'Emirates Steel', 'Mubadala', 'EGA', 'DP World']

const FEATURES = [
  {
    icon: GitBranch,
    title: 'Role-based workflows',
    copy: 'Four roles with permissions baked into every route — not layered on after the fact.',
  },
  {
    icon: Sparkles,
    title: 'AI comparison reports',
    copy: 'Ingest vendor proposals and surface a defensible side-by-side recommendation in minutes.',
  },
  {
    icon: FileText,
    title: 'Contract auto-draft',
    copy: 'Draft a contract from awarded terms, ready for legal review in one step.',
  },
  {
    icon: ShieldCheck,
    title: 'Immutable audit trail',
    copy: 'Every upload, edit and approval is signed, timestamped and exportable.',
  },
  {
    icon: LineChart,
    title: 'Spend intelligence',
    copy: 'Budget vs actual, cycle time and vendor mix — without a separate BI ticket.',
  },
  {
    icon: Lock,
    title: 'Enterprise security',
    copy: 'SOC 2 aligned, SSO-ready, tenant-scoped. Vendors only see what you invite them to.',
  },
] as const

const STEPS = [
  {
    n: '01',
    title: 'Set budget & scope',
    copy: 'Business Admin creates the project envelope and invites a Project Lead.',
  },
  {
    n: '02',
    title: 'Invite vendors',
    copy: 'Each vendor gets a scoped link — no shared inboxes or misrouted PDFs.',
  },
  {
    n: '03',
    title: 'Collect proposals',
    copy: 'Guided checklist; submissions lock unless returned for edits.',
  },
  {
    n: '04',
    title: 'Run AI comparison',
    copy: 'Pipeline scores technical, commercial and compliance dimensions.',
  },
  {
    n: '05',
    title: 'Award & contract',
    copy: 'One click drafts the contract from awarded terms — signed and filed.',
  },
] as const

const ROLES = [
  {
    name: 'Business Admin',
    color: '#3b82f6',
    copy: 'Full edit access to projects, reports and invoices.',
  },
  {
    name: 'Project Lead',
    color: '#10b981',
    copy: 'Owns projects; runs ingestion, reviews reports, signs contracts.',
  },
  {
    name: 'Vendor Lead',
    color: '#f59e0b',
    copy: 'Scoped to invited projects; uploads and submits only.',
  },
  {
    name: 'Technology Admin',
    color: '#64748b',
    copy: 'Read-only everywhere for maintenance. Never edits vendor data.',
  },
] as const

export function Landing(): JSX.Element {
  return (
    <div className="bks-public min-h-screen bg-white text-[var(--ink)]">
      <header className="sticky top-0 z-30 public-header">
        <div className="max-w-6xl mx-auto px-6 h-[4.25rem] flex items-center justify-between">
          <Link to="/" className="flex items-center gap-2.5">
            <ShieldMark size={28} tone="dark" />
            <span className="font-semibold text-[15px] tracking-tight">{BRAND_NAME}</span>
          </Link>
          <nav className="hidden md:flex items-center gap-7">
            <a href="#product" className="public-nav-link">
              Product
            </a>
            <a href="#workflow" className="public-nav-link">
              Workflow
            </a>
            <a href="#roles" className="public-nav-link">
              Security
            </a>
            <a href="#contact" className="public-nav-link">
              Contact
            </a>
          </nav>
          <div className="flex items-center gap-2">
            <Link to="/login" className="hidden sm:inline-flex public-nav-link px-3 py-2">
              Sign in
            </Link>
            <Link
              to="/login"
              className="btn-navy h-9 px-4 text-[13px] inline-flex items-center gap-2"
            >
              Get started <ArrowRight size={14} />
            </Link>
          </div>
        </div>
      </header>

      <section className="hero-wash">
        <div className="max-w-6xl mx-auto px-6 pt-16 pb-20 grid grid-cols-1 lg:grid-cols-12 gap-14 items-center">
          <div className="lg:col-span-7 fade-up">
            <span className="chip">
              <ShieldCheck size={12} /> Enterprise procurement
            </span>
            <h1 className="display-heading mt-6 text-[2.75rem] md:text-5xl xl:text-[3.25rem]">
              Procurement &amp; cost estimation,{' '}
              <span className="text-[var(--navy-800)]">governed end to end.</span>
            </h1>
            <p className="mt-5 text-[16px] leading-relaxed text-[var(--muted)] max-w-[540px]">
              From vendor invitations to AI-scored comparisons and signed contracts — one workspace
              with the guardrails your procurement office needs.
            </p>
            <div className="mt-7 flex flex-wrap items-center gap-3">
              <Link
                to="/login"
                className="btn-navy h-11 px-5 text-[14px] inline-flex items-center gap-2"
              >
                Enter workspace <ArrowRight size={15} />
              </Link>
              <a href="#workflow" className="btn-ghost h-11 px-5 text-[14px] inline-flex items-center">
                See the workflow
              </a>
            </div>
            <div className="trust-row mt-8">
              <span>No credit card</span>
              <span aria-hidden>·</span>
              <span>Four demo roles</span>
              <span aria-hidden>·</span>
              <span>Full audit trail</span>
            </div>
          </div>

          <div className="lg:col-span-5 fade-up" style={{ animationDelay: '80ms' }}>
            <div className="card-flat p-6 shadow-[0_20px_50px_-24px_rgba(15,23,42,0.18)]">
              <div className="flex items-center justify-between gap-3">
                <div className="eyebrow">Comparison report</div>
                <span className="chip chip-soft">AI scored</span>
              </div>
              <div className="mt-3 text-[15px] font-semibold">ADH-SUB-40KV-2026 · 3 vendors</div>
              <div className="mt-5 space-y-4">
                {[
                  { name: 'Siemens Energy', pct: 92, delta: '-3.1%', tone: 'text-emerald-600' },
                  { name: 'ABB', pct: 87, delta: '+1.9%', tone: 'text-amber-600' },
                  { name: 'Hitachi Energy', pct: 74, delta: '—', tone: 'text-[var(--muted)]' },
                ].map((v) => (
                  <div key={v.name}>
                    <div className="flex justify-between text-[13px]">
                      <span className="font-medium text-[var(--ink)]">{v.name}</span>
                      <span className={`font-medium ${v.tone}`}>{v.delta}</span>
                    </div>
                    <div className="mt-2 h-1 bg-[var(--line-2)] rounded-full overflow-hidden">
                      <div
                        className="h-full bg-[var(--navy-800)] rounded-full"
                        style={{ width: `${v.pct}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-5 pt-4 border-t border-[var(--line)] text-[12px] text-[var(--muted)]">
                Generated in 4.2s · technical + commercial + compliance
              </div>
            </div>
          </div>
        </div>

        <div className="client-strip">
          <div className="max-w-6xl mx-auto px-6 py-5 flex flex-wrap items-center justify-center gap-x-8 gap-y-2">
            <span className="eyebrow !text-[10px]">Built for enterprises like</span>
            {CLIENTS.map((c) => (
              <span key={c} className="client-name">
                {c}
              </span>
            ))}
          </div>
        </div>
      </section>

      <section id="product" className="max-w-6xl mx-auto px-6 section-block">
        <div className="max-w-xl">
          <span className="eyebrow">Product</span>
          <h2 className="mt-3 text-3xl md:text-4xl font-bold tracking-tight">
            One workspace. Every safeguard.
          </h2>
          <p className="mt-3 text-[15px] leading-relaxed text-[var(--muted)]">
            Vendor RFP through award and contract in a single audited pipeline — AI where it
            matters, humans where it counts.
          </p>
        </div>
        <div className="mt-10 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {FEATURES.map(({ icon: Icon, title, copy }) => (
            <div key={title} className="card-flat card-flat-hover p-5">
              <div className="w-9 h-9 rounded-md bg-[var(--paper-2)] flex items-center justify-center text-[var(--navy-800)]">
                <Icon size={17} strokeWidth={1.75} />
              </div>
              <h3 className="mt-3.5 text-[15px] font-semibold">{title}</h3>
              <p className="mt-1.5 text-[13.5px] leading-relaxed text-[var(--muted)]">{copy}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="workflow" className="login-mesh text-white section-block">
        <div className="max-w-6xl mx-auto px-6">
          <div className="max-w-xl">
            <span className="eyebrow text-white/50">Workflow</span>
            <h2 className="mt-3 text-3xl md:text-4xl font-bold tracking-tight">
              Scope to signature in five steps.
            </h2>
            <p className="mt-3 text-[15px] leading-relaxed text-white/65">
              Each step is scoped to the right role, timestamped, and reversible only via a signed
              audit event.
            </p>
          </div>
          <ol className="mt-10 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-3">
            {STEPS.map((s) => (
              <li key={s.n} className="workflow-step">
                <div className="mono text-[11px] text-white/45">{s.n}</div>
                <div className="mt-2 text-[14px] font-semibold">{s.title}</div>
                <p className="mt-1.5 text-[13px] leading-relaxed text-white/55">{s.copy}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section id="roles" className="max-w-6xl mx-auto px-6 section-block">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 items-start">
          <div>
            <span className="eyebrow">Security</span>
            <h2 className="mt-3 text-3xl md:text-4xl font-bold tracking-tight">
              Four roles. Zero over-permission.
            </h2>
            <p className="mt-3 text-[15px] leading-relaxed text-[var(--muted)]">
              Permissions are enforced on every route and control. Try any role from the sign-in
              page in seconds.
            </p>
            <div className="mt-6 space-y-2.5">
              {ROLES.map((r) => (
                <div
                  key={r.name}
                  className="flex items-start gap-3 p-4 rounded-[var(--radius)] border border-[var(--line)] bg-[var(--paper-2)]"
                >
                  <span className="mt-1.5 role-dot" style={{ background: r.color }} />
                  <div>
                    <div className="text-[14px] font-semibold">{r.name}</div>
                    <div className="text-[13px] text-[var(--muted)] mt-0.5">{r.copy}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="card-flat p-6">
            <div className="eyebrow">Recent audit events</div>
            <div className="mt-4">
              {[
                {
                  who: 'Karim El Sayed',
                  role: 'Project Lead',
                  action: 'Ran ingestion pipeline',
                  project: 'ADH-SUB-40KV-2026',
                },
                {
                  who: 'Sarah Al Mansoori',
                  role: 'Business Admin',
                  action: 'Approved shortlist',
                  project: 'ADH-SUB-40KV-2026',
                },
                {
                  who: 'System',
                  role: 'Audit',
                  action: 'Chain-signed event batch',
                  project: '12 events · 4.2s',
                },
              ].map((e) => (
                <div key={e.who + e.action} className="audit-line">
                  <div className="w-8 h-8 rounded-full bg-[var(--navy-800)] text-white flex items-center justify-center text-[10px] font-bold shrink-0">
                    {e.who.slice(0, 2).toUpperCase()}
                  </div>
                  <div className="min-w-0">
                    <div className="text-[13px] font-semibold">{e.action}</div>
                    <div className="text-[12px] text-[var(--muted)] mt-0.5">
                      {e.who} · {e.role} · {e.project}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section id="contact" className="max-w-6xl mx-auto px-6 pb-20">
        <div className="rounded-[var(--radius)] login-mesh text-white px-10 py-12 md:px-12">
          <h2 className="display-heading text-3xl md:text-4xl max-w-2xl">
            Try the workspace with a demo role in under 30 seconds.
          </h2>
          <p className="mt-3 text-[15px] text-white/65 max-w-lg">
            Pick any demo account on the sign-in page. No setup, no sales call.
          </p>
          <div className="mt-7 flex flex-wrap gap-3">
            <Link
              to="/login"
              className="h-11 px-5 rounded-[var(--radius)] bg-white text-[var(--navy-800)] font-semibold text-[14px] inline-flex items-center gap-2 hover:bg-white/95"
            >
              Open the demo <ArrowRight size={15} />
            </Link>
            <a
              href="mailto:hello@bks.ai"
              className="btn-ghost h-11 px-5 text-[14px] inline-flex items-center !border-white/20 !text-white !bg-transparent hover:!bg-white/10"
            >
              Talk to procurement ops
            </a>
          </div>
        </div>
      </section>

      <footer className="border-t border-[var(--line)]">
        <div className="max-w-6xl mx-auto px-6 py-6 flex flex-wrap items-center justify-between gap-3 text-[12px] text-[var(--muted)]">
          <div className="flex items-center gap-2">
            <ShieldMark size={20} tone="dark" />
            <span className="font-semibold text-[var(--ink-2)]">{BRAND_NAME}</span>
          </div>
          <span>© 2026 BKS AI · SOC 2 aligned · Abu Dhabi</span>
        </div>
      </footer>
    </div>
  )
}
