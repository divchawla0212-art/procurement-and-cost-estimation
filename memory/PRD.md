# BKS AI Procurement — Enterprise Frontend Uplift

## Original problem statement
- Existing FastAPI + React/Vite/TS procurement workspace under `/app/web` (frontend) and `/app/api` (backend).
- User request: **enterprise-level frontend**, do **not** touch the backend, make buttons and page alignment feel proper.
- Additional stated backlog: **Workflow Search** — quick search + saved filters across projects, RFQs, vendors, compliance records.

## Architecture (unchanged)
- Backend: FastAPI at `/app/api/main.py` — thin shim at `/app/backend/server.py` re-exports the same app on port 8001 so the platform supervisor can boot it. Zero business-logic changes.
- Frontend: React 19 + Vite 8 + TypeScript under `/app/web` (symlinked to `/app/frontend` for supervisor).
- Session cookie flow (`/api/auth/*`) unchanged. Bootstrap seed reads `ADMIN_EMAIL` / `ADMIN_PASSWORD` from `/app/.env`.

## What is implemented
Feature set is preserved as-is. All the changes are **frontend visual polish + one net-new feature**.

### Global Workflow Search (⌘K / Ctrl+K) — NEW
- Command-palette-style overlay that quick-searches across:
  - Projects (bid-set projects), Workflow projects, RFQs, Vendors (bidders + per-project vendors), Compliance-matrix entries
- Scope chips (All / Projects / Workflow / RFQs / Vendors / Compliance) to filter the surface.
- **Saved filters** — persisted to `localStorage` under `bks:global-search:saved-filters`; chips at top of the palette apply the stored query + scope in one click and can be removed inline.
- Full keyboard support (↑ ↓ Enter Esc, ⌘K anywhere).
- Wired into the top status bar via a pill-shaped trigger with `⌘K` kbd hint (replaces the raw `API :8000` text).
- File: `/app/web/src/components/GlobalSearch.tsx` (+ styles appended to `/app/web/src/theme.css`).

### Enterprise UI polish
- **Sign-in**: primary button now has label centred with the chevron flush right, larger height, tactile hover; demo tiles refactored to consistent height with subtle `READY` / `ASK ADMIN` badges and a proper "Tap a tile to fill credentials" caption.
- **Buttons (`.btn`, `.btn-primary`, `.btn-ink`, `.btn-sm`)**: consistent heights (2.3rem / 1.9rem / 2.75rem), pill radius, restrained shadow, one hover treatment across the whole app.
- **Status bar**: cleaner spacing, pill-shaped item badges, global-search trigger on the right, removed dead "API :8000" text.
- **Sidebar sign-out** button: bigger tap target, refined border/hover.
- **State placeholders** (`EmptyState`, `ErrorState`): swapped ASCII glyphs for `lucide` icons (Inbox / AlertTriangle) and toned the glyph box down.
- **Forms** (`.form-grid`): now responsive 2–3 column grid (`repeat(auto-fit, minmax(220px, 1fr))`), with `.form-row-wide` and `.form-actions` slot classes so long fields span the full row and submit/cancel align on one line.
- Applied consistently to `New project` form and `Users and access › Add a user` form.
- **API layer**: on any 401 response the client now redirects to `/login?returnTo=…` — replaces the bare "authentication required" state a stale session left behind.

## Sample data (dev only)
Seeded via public API to make the palette immediately demonstrable:
- Workflow projects: `Abu Dhabi 400kV Substation (ADH-SUB-400KV-2026)`, `Fujairah LNG Terminal (FJR-LNG-2026)`, `Sohar Solar Farm (SOH-SOL-2026)`.

## Prioritised backlog
### P0 — none open
### P1
- Populate compliance records / bidder registry with realistic sample data so the palette groups show more variety end-to-end.
- Mobile: collapse the sidebar to a bottom-tab drawer for narrow viewports.
### P2
- Persist saved-filter order (drag to reorder) and share filters across a team via a `/api/workflow/saved-filters` route (would require a small backend addition — explicitly deferred, backend untouched).
- ⌘K deep integration: `/g p` type prefix commands, recent items, keyboard shortcut cheatsheet.

## Key files touched
- `/app/backend/server.py` (NEW — supervisor shim; no logic)
- `/app/web/vite.config.ts` (proxy `/api` → 8001, bind `0.0.0.0`)
- `/app/web/package.json` (added `start` script)
- `/app/web/src/App.tsx` (statusbar search trigger + palette mount + ⌘K keybinding)
- `/app/web/src/components/GlobalSearch.tsx` (NEW)
- `/app/web/src/pages/Auth.tsx` (button + demo tile polish)
- `/app/web/src/pages/Admin.tsx` (form uses .form-grid + form-actions)
- `/app/web/src/pages/forms.tsx` (Actions component uses .form-actions)
- `/app/web/src/components/primitives.tsx` (EmptyState/ErrorState use lucide icons)
- `/app/web/src/api.ts` (401 → /login redirect)
- `/app/web/src/theme.css` (button + statusbar + rail + form + palette styles)
- `/app/web/src/public-ui.css` (auth button + demo tile styles)
