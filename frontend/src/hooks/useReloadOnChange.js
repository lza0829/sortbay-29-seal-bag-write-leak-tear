import { useEffect, useRef } from 'react'
import { DATA_CHANGED_EVENT } from '../api'

export function useReloadOnChange(load) {
  const saved = useRef(load)
  saved.current = load

  useEffect(() => {
    const run = () => {
      try {
        const result = saved.current?.()
        if (result && typeof result.catch === 'function') result.catch(() => {})
      } catch {
        // Pages own their visible error state; refresh listeners must not throw.
      }
    }
    const onVisibility = () => {
      if (document.visibilityState === 'visible') run()
    }
    window.addEventListener(DATA_CHANGED_EVENT, run)
    window.addEventListener('focus', run)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      window.removeEventListener(DATA_CHANGED_EVENT, run)
      window.removeEventListener('focus', run)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])
}
