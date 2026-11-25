import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { useReloadOnChange } from '../hooks/useReloadOnChange'

const surface_panel = true
export default function Chutes() {
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = () => api('/口门在口').then(setRows).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  useReloadOnChange(load)

  const toggle = async (id, 状态) => {
    setErr(''); setMsg('')
    try {
      await api(`/口门/${id}/开闭?状态=${encodeURIComponent(状态)}`, { method: 'POST' })
      setMsg(`口门已${状态 === '口门开' ? '开' : '闭'}，在口投影已同刷`)
      load()
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className={surface_panel ? "panel" : "panel"}>
      <h2>口门在口 · 口门页</h2>
      <p className="muted">封口后，已封袋件不再计入“在口未封件”，但会单独显示已封袋件数与同一封口事件，避免把封条态和在口态混成一个数。</p>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}
      <table>
        <thead>
          <tr>
            <th>口门码</th><th>标题</th><th>状态</th><th>软容量</th>
            <th>在口件数</th><th>未入袋</th><th>集包中</th><th>已封袋</th>
            <th>当前袋/封条事件</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id}>
              <td>{c.code}</td>
              <td>{c.title}</td>
              <td><span className={`badge ${c.status === '口门开' ? 'badge-open' : 'badge-locked'}`}>{c.status}</span></td>
              <td>{c.soft_cap}</td>
              <td><strong>{c.在口件数}</strong></td>
              <td>{c.未入袋件数}</td>
              <td>{c.集包中件数}</td>
              <td>{c.已封袋件数}</td>
              <td>
                {c.集包中袋码 && <div>集包：{c.集包中袋码}</div>}
                {c.已封袋袋码 && <div className="locked-text">已封：{c.已封袋袋码}</div>}
                {c.封口事件编号 && <code className="event-code">{c.封口事件编号}</code>}
              </td>
              <td>
                <button type="button" onClick={() => toggle(c.id, '口门开')}>开</button>{' '}
                <button type="button" className="secondary" onClick={() => toggle(c.id, '口门闭')}>闭</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
