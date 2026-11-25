import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { useReloadOnChange } from '../hooks/useReloadOnChange'

const surface_panel = true
export default function Diverts() {
  const [batches, setBatches] = useState([])
  const [scans, setScans] = useState([])
  const [scanId, setScanId] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = async () => {
    setBatches(await api('/回流批次'))
    setScans(await api('/扫描'))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  useReloadOnChange(load)

  const divertable = useMemo(() => scans.filter((x) => x.回流允许), [scans])
  const lockedSealed = useMemo(() => scans.filter((x) => x.封袋已封), [scans])
  const selectableScans = useMemo(() => {
    const ids = new Set()
    return [...divertable, ...lockedSealed].filter((scan) => {
      if (ids.has(scan.id)) return false
      ids.add(scan.id)
      return true
    })
  }, [divertable, lockedSealed])

  const create = async (forcedScanId = null) => {
    const id = forcedScanId || Number(scanId)
    if (!id) return
    setErr(''); setMsg('')
    try {
      const result = await api('/回流批次', { method: 'POST', body: JSON.stringify({ 扫描编号: id }) })
      setMsg(`回流批次 ${result.批次?.code || ''} 已建立`)
      if (!forcedScanId) setScanId('')
      load()
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className={surface_panel ? "panel" : "panel"}>
      <h2>错分回流</h2>
      <p className="muted">采用全局唯一架构 A · 袋体写边界全关口径：未封袋内件可回流纠偏；一旦封口事件落下，回流写口由同一封条事件关闭。</p>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}
      <div className="row">
        <label>扫描
          <select value={scanId} onChange={(e) => setScanId(e.target.value)}>
            <option value="">选择回流件（已封件也保留，点击后验证拒绝）</option>
            {selectableScans.map((s) => (
              <option key={s.id} value={s.id}>
                {s.id} · {s.barcode} · {s.status}{s.袋码 ? ` · ${s.袋码}` : ''}{s.封袋已封 ? ' · 已封应拒绝' : ''}
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => create()} disabled={!scanId}>建回流批次</button>
      </div>

      <h3>封袋锁定件（回流写口已关）</h3>
      <table>
        <thead><tr><th>编号</th><th>条码</th><th>袋码</th><th>封口事件</th><th>回流操作</th></tr></thead>
        <tbody>
          {lockedSealed.map((s) => (
            <tr key={s.id} className="locked-row">
              <td>{s.id}</td><td>{s.barcode}</td><td>{s.袋码}</td>
              <td className="event-cell"><code>{s.封口事件编号}</code></td>
              <td>
                <button
                  type="button"
                  title="袋体写边界全关；点击将由后端拒绝并保留审计/错误提示"
                  onClick={() => create(s.id)}
                >
                  回流
                </button>
              </td>
            </tr>
          ))}
          {!lockedSealed.length && <tr><td colSpan={5} className="muted">暂无已封袋锁定件</td></tr>}
        </tbody>
      </table>

      <h3>回流批次</h3>
      <table>
        <thead><tr><th>编号</th><th>批次码</th><th>状态</th><th>时间</th><th>备注</th></tr></thead>
        <tbody>
          {batches.map((b) => (
            <tr key={b.id}><td>{b.id}</td><td>{b.code}</td><td>{b.status}</td><td>{b.created_at}</td><td>{b.note}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
