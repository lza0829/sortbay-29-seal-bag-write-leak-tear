export const DATA_CHANGED_EVENT = 'sortbay:data-changed'

export async function api(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase()
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  const text = await res.text()
  let data
  try { data = text ? JSON.parse(text) : null } catch { data = { 明细: text } }
  if (!res.ok) {
    const msg = (data && (data.detail || data.明细)) || res.statusText
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  if (method !== 'GET' && typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(DATA_CHANGED_EVENT, { detail: { path, data } }))
  }
  return data
}
