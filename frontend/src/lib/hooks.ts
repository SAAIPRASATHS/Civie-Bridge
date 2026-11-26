/**
 * Small data-loading helpers shared by the dashboard pages.
 *
 * There is no global cache: each page owns its own request, so a screen never
 * shows a figure that is current only because some other screen refreshed it.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { DependencyList } from 'react'

export interface AsyncState<T> {
  data: T | null
  error: unknown
  isLoading: boolean
  /** Re-run the loader, e.g. after an officer decision changes the record. */
  reload: () => void
}

/**
 * Run `load` whenever `deps` change and expose the three states a panel needs.
 *
 * Results from a superseded request are discarded, so a slow first request can
 * never overwrite the result of a newer one.
 */
export function useAsyncData<T>(load: () => Promise<T>, deps: DependencyList): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [isLoading, setLoading] = useState(true)
  const [attempt, setAttempt] = useState(0)

  // Keep the latest loader without making it a dependency, so an inline arrow
  // function at the call site does not trigger a request on every render.
  const loadRef = useRef(load)
  loadRef.current = load

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    loadRef
      .current()
      .then((result) => {
        if (!cancelled) setData(result)
      })
      .catch((failure: unknown) => {
        if (!cancelled) setError(failure)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, attempt])

  const reload = useCallback(() => setAttempt((value) => value + 1), [])

  return { data, error, isLoading, reload }
}

/** Set the document title so browser history and tabs stay readable. */
export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = `${title} · CivicBridge AI`
  }, [title])
}
