import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { useReloadOnChange } from '../hooks/useReloadOnChange'

const surface_panel = true
export default function RoutesPage() {
  const [rows, setRows] = useState([])
  const [chutes, setChutes] = useState([])
  const [dest, setDest] = useState('')
  const [chuteId, setChuteId] = useState('')
  const [note, setNote] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = async () => {
    setRows(await api('/路由表'))
    const cs = await api('/口门')
    setChutes(cs)
    setChuteId((old) => old || (cs[0] ? String(cs[0].id) : ''))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  useReloadOnChange(load)

  const save = async () => {
    setErr(''); setMsg('')
    try {
      await api('/路由表', {
        method: 'POST',
        body: JSON.stringify({ 目的地码: dest, 口门编号: Number(chuteId), 备注: note }),
      })
      setDest(''); setNote('')
      setMsg('路由写入成功')
      load()
    } catch (e) { setErr(e.message) }
  }

  const edit = (r) => {
    setDest(r.dest_code)
    setChuteId(String(r.chute_id))
    setNote(r.note || '')
  }

  return (
    <div className={surface_panel ? "panel" : "panel"}>
      <h2>改路由 · 路由表</h2>
      <p className="muted">架构 A · 袋体写边界全关：若目的地已有条码在封袋中，变更该目的地的口门会被封口事件闸拒绝；历史路由只按封口事件重放，不做封后脏写。</p>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}
      <div className="row">
        <label>目的地码<input value={dest} onChange={(e) => setDest(e.target.value)} /></label>
        <label>口门
          <select value={chuteId} onChange={(e) => setChuteId(e.target.value)}>
            {chutes.map((c) => <option key={c.id} value={c.id}>{c.code} {c.title}</option>)}
          </select>
        </label>
        <label>备注<input value={note} onChange={(e) => setNote(e.target.value)} /></label>
        <button type="button" onClick={save} title="写入路由表；若口门变化即改路由">写入路由</button>
      </div>
      <table>
        <thead><tr><th>目的地码</th><th>口门</th><th>备注</th><th>封袋改路由闸</th><th>操作</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className={r.封袋锁定 ? 'locked-row' : ''}>
              <td>{r.dest_code}</td>
              <td>{r.口门码}</td>
              <td>{r.note}</td>
              <td>
                {r.封袋锁定 ? (
                  <span className="chip locked">已封 {r.已封袋件数} 件 · 改路由拒绝<br /><code>{r.封口事件编号}</code></span>
                ) : <span className="chip ok">未封锁定</span>}
              </td>
              <td><button type="button" className="secondary" onClick={() => edit(r)}>填入修改</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
