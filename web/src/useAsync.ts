import { useEffect, useState } from 'react'

export interface AsyncState<T> {
  data: T | null
  error: string | null
  loading: boolean
}

/**
 * Run an async loader when `deps` change, with cancellation so a stale
 * response never overwrites a newer one.
 */
export function useAsync<T>(
  loader: () => Promise<T>,
  deps: unknown[],
): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    error: null,
    loading: true,
  })

  useEffect(() => {
    let cancelled = false
    setState({ data: null, error: null, loading: true })
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
