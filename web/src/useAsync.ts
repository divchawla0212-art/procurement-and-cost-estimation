import { useEffect, useState } from 'react'

export interface AsyncState<T> {
  data: T | null
  error: string | null
  loading: boolean
}

export interface AsyncOptions {
  /**
   * Keep the previous `data` on screen while a re-run is in flight, instead
   * of blanking it.
   *
   * Off by default, and that default is the safe one: most callers here key
   * on a project slug, where showing one project's numbers under another
   * project's name for a few hundred milliseconds would be worse than a
   * spinner. Turn it on only where every run loads the *same* collection —
   * a refresh, not a switch. Without it a page that re-runs on every
   * mutation unmounts its own controls mid-interaction, which takes the
   * keyboard focus and any live-region announcement with it.
   */
  keepPreviousData?: boolean
}

/**
 * Run an async loader when `deps` change, with cancellation so a stale
 * response never overwrites a newer one.
 */
export function useAsync<T>(
  loader: () => Promise<T>,
  deps: unknown[],
  options: AsyncOptions = {},
): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    error: null,
    loading: true,
  })

  useEffect(() => {
    let cancelled = false
    setState((prev) => ({
      data: options.keepPreviousData ? prev.data : null,
      error: null,
      loading: true,
    }))
    loader()
      .then((data) => {
        if (!cancelled) setState({ data, error: null, loading: false })
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ data: null, error: err.message, loading: false })
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return state
}
