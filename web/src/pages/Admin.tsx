import { useState } from 'react'
import type { AdminUser, ProjectSummary } from '../types'
import {
  createUser,
  deleteUser,
  fetchUsers,
  grantProject,
  revokeProject,
} from '../api'
import { useAsync } from '../useAsync'
import { MIN_PASSWORD } from '../auth/context'
import {
  Card,
  ErrorState,
  LoadingState,
  PageHeader,
  PasswordField,
} from '../components/primitives'

export interface AdminProps {
  /**
   * The signed-in admin's project list, which for an admin is every project —
   * the same list the rail's switcher shows. Passed in rather than fetched
   * again so the toggles here and the switcher there can never disagree.
   */
  projects: ProjectSummary[]
  /** The caller's own id, so the row for it can say why it has no Remove. */
  meId: string
}

function formatDate(iso: string): string {
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleDateString()
}

export function Admin({ projects, meId }: AdminProps) {
  const [tick, setTick] = useState(0)
  // `keepPreviousData` because every mutation on this screen re-runs this
  // loader. Blanking the list would unmount the very chip that was just
  // clicked, so keyboard focus would land back on <body> after each toggle
  // and a screen reader would never hear the aria-pressed change — it would
  // hear the table disappear. Every run loads the same collection, so there
  // is no stale-identity hazard in holding the old rows for a moment.
  const { data: users, error, loading } = useAsync<AdminUser[]>(
    fetchUsers,
    [tick],
    { keepPreviousData: true },
  )

  // The key of the control with a request in flight — `${userId}:${slug}` for
  // a grant, the plain user id for a removal — so only that control goes
  // disabled. Two overlapping clicks are harmless (grant is idempotent,
  // revoke is a no-op), so this disables rather than serializes.
  const [busy, setBusy] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<'admin' | 'reviewer'>('reviewer')
  const [adding, setAdding] = useState(false)
  const [added, setAdded] = useState<string | null>(null)

  const reload = () => setTick((t) => t + 1)

  /**
   * Run a mutation, then reload from the server rather than patching local
   * state. The reload is the point: `granted_slugs` is read fresh on every
   * request, so what the next screen enforces is what the server holds, not
   * what this component believes it wrote.
   */
  async function run(key: string, action: () => Promise<void>) {
    setBusy(key)
    setFailure(null)
    try {
      await action()
      reload()
    } catch (err) {
      setFailure((err as Error).message)
    } finally {
      // Clear only if this call is still the one holding the key — a later
      // click that overtook it must not have its own control re-enabled by
      // an earlier request finishing.
      setBusy((cur) => (cur === key ? null : cur))
    }
  }

  function toggleGrant(user: AdminUser, slug: string) {
    const key = `${user.id}:${slug}`
    // The chip is marked `aria-disabled` rather than `disabled` while its
    // request is in flight, so the guard against a second click has to live
    // here — a truly disabled button would refuse the click for us, but it
    // would also be dropped from the tab order mid-interaction, which blurs
    // it and drops the keyboard user back to the top of the page.
    if (busy === key) return Promise.resolve()
    const granted = user.grants.includes(slug)
    return run(key, () =>
      granted ? revokeProject(user.id, slug) : grantProject(user.id, slug),
    )
  }

  async function submitNewUser(e: React.FormEvent) {
    e.preventDefault()
    setAdding(true)
    setFailure(null)
    setAdded(null)
    try {
      const user = await createUser(email.trim(), password, role)
      setAdded(user.email)
      setEmail('')
      setPassword('')
      setRole('reviewer')
      reload()
    } catch (err) {
      setFailure((err as Error).message)
    } finally {
      setAdding(false)
    }
  }

  // Only the first load blanks the screen; a refresh keeps the table up.
  if (loading && !users) return <LoadingState label="Loading users…" />
  if (error) return <ErrorState message={error} />
  if (!users) return null

  return (
    <>
      <PageHeader
        eyebrow="Access"
        title="Users and access"
        sub={
          <>
            Reviewers see only the projects you grant them. Admins see every
            project, so their access is not listed per project here.
          </>
        }
      />

      {failure && (
        <div className="banner banner--error" data-testid="admin-error-message" role="alert">
          {failure}
        </div>
      )}
      {added && (
        <div className="banner banner--ok" data-testid="admin-success-message" role="status">
          Added {added}. They can sign in with the password you set.
        </div>
      )}

      <Card title="Add a user">
        <form onSubmit={submitNewUser} data-testid="add-user-form">
          <div className="form-row">
            <label htmlFor="new-email">Email</label>
            <input
              id="new-email"
              className="field"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="off"
              required
              disabled={adding}
              data-testid="new-user-email-input"
            />
          </div>
          <div className="form-row">
            <label htmlFor="new-password">Password</label>
            <PasswordField
              id="new-password"
              className="field"
              value={password}
              onChange={setPassword}
              minLength={MIN_PASSWORD}
              autoComplete="new-password"
              required
              disabled={adding}
            />
            <p className="muted" style={{ fontSize: '0.8rem', margin: '0.3rem 0 0' }}>
              At least {MIN_PASSWORD} characters. Share it with them directly —
              they can change it once they sign in.
            </p>
          </div>
          <div className="form-row">
            <label htmlFor="new-role">Role</label>
            <select
              id="new-role"
              className="field"
              value={role}
              onChange={(e) => setRole(e.target.value as 'admin' | 'reviewer')}
              disabled={adding}
              data-testid="new-user-role-select"
            >
              <option value="reviewer">Reviewer — sees granted projects</option>
              <option value="admin">Admin — sees everything, manages users</option>
            </select>
          </div>
          <button type="submit" className="btn btn-primary" data-testid="add-user-submit-button" disabled={adding}>
            {adding ? 'Adding…' : 'Add user'}
          </button>
        </form>
      </Card>

      <Card title={`Users (${users.length})`}>
        {projects.length === 0 && (
          <div className="banner banner--warn">
            No projects yet, so there is nothing to grant. Set one up first.
          </div>
        )}
        <div className="tbl-wrap" data-testid="users-table-container">
          <table className="grid" data-testid="users-table">
            <thead>
              <tr>
                <th>User</th>
                <th>Role</th>
                <th>Added</th>
                <th>Project access</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {users.map((user) => {
                const isMe = user.id === meId
                return (
                  <tr key={user.id}>
                    <td>
                      {user.email}
                      {isMe && <span className="muted"> — you</span>}
                    </td>
                    <td>{user.role}</td>
                    <td className="muted mono">{formatDate(user.created_at)}</td>
                    <td>
                      {user.role === 'admin' ? (
                        <span className="muted">Every project</span>
                      ) : projects.length === 0 ? (
                        <span className="muted">—</span>
                      ) : (
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
                          {projects.map((p) => {
                            const on = user.grants.includes(p.slug)
                            const key = `${user.id}:${p.slug}`
                            // Named for the person as well as the project.
                            // The visible text is just the project, which
                            // reads identically in every row, and a `title`
                            // does not become the accessible name when the
                            // button already has text — so without the
                            // aria-label a screen reader announces the same
                            // "Tender One, pressed" for every reviewer.
                            const label = on
                              ? `Remove ${user.email}'s access to ${p.name}`
                              : `Give ${user.email} access to ${p.name}`
                            return (
                              <button
                                key={p.slug}
                                type="button"
                                className={on ? 'chip on' : 'chip'}
                                aria-pressed={on}
                                aria-label={label}
                                aria-disabled={busy === key}
                                onClick={() => toggleGrant(user, p.slug)}
                                title={label}
                              >
                                {p.name}
                              </button>
                            )
                          })}
                        </div>
                      )}
                    </td>
                    <td>
                      {isMe ? (
                        // The server refuses this too (409). Saying so here is
                        // kinder than offering a button that always fails.
                        <span className="muted" style={{ fontSize: '0.8rem' }}>
                          Sign out to leave
                        </span>
                      ) : confirming === user.id ? (
                        <div style={{ display: 'flex', gap: '0.35rem' }}>
                          <button
                            type="button"
                            className="btn btn-sm btn-danger"
                            disabled={busy === user.id}
                            onClick={() =>
                              run(user.id, () => deleteUser(user.id)).then(() =>
                                setConfirming(null),
                              )
                            }
                          >
                            {busy === user.id ? 'Removing…' : 'Remove for good'}
                          </button>
                          <button
                            type="button"
                            className="btn btn-sm btn-ghost"
                            onClick={() => setConfirming(null)}
                          >
                            Keep
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => setConfirming(user.id)}
                        >
                          Remove
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  )
}
