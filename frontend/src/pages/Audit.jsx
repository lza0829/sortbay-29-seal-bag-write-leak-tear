import React, { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { useReloadOnChange } from '../hooks/useReloadOnChange'

export default function Audit() {
  const [rows, setRows] = useState([])
  const [eventRef, setEventRef] = useState('')
  const [replay, setReplay] = useState(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [searchParams, setSearchParams] = useSearchParams()

  const load = () => api('/审计').then(setRows).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  useReloadOnChange(load)

  const runReplay = async (ref = eventRef) => {
    const value = String(ref || '').trim()
    setErr(''); setMsg(''); setReplay(null)
    if (!value) {
      setErr('请输入封口事件编号或审计编号')
      return
    }
    try {
      const data = await api(`/审计/封口事件/${encodeURIComponent(value)}/重放`)
      setReplay(data)
      setEventRef(value)
      setSearchParams({ event_id: value }, { replace: true })
      setMsg('已由同一封口事件重放全部联动投影')
      const related = await api('/审计', {}) // ensure audit list is fresh after navigation/deep link
      setRows(related)
    } catch (e) {
      setErr(e.message)
    }
  }

  useEffect(() => {
    const eventId = searchParams.get('event_id')
    if (eventId) {
      setEventRef(eventId)
      runReplay(eventId)
    }
    // Run once for deep links such as /审计?event_id=...
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="panel">
      <h2>审计重放</h2>
      <p className="muted">封口事件是重放锚点：同一事件可还原封袋写闸、扫描册条码、口门在口、集包袋册、总览对账与审计记录。</p>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}

      <div className="row">
        <label>封口事件编号 / 审计编号
          <input value={eventRef} onChange={(e) => setEventRef(e.target.value)} placeholder="seal-bag-... 或数字" />
        </label>
        <button type="button" onClick={() => runReplay()}>重放封口事件</button>
        {eventRef && <button type="button" className="secondary" onClick={() => api(`/审计?event_id=${encodeURIComponent(eventRef)}`).then(setRows)}>只筛该事件审计</button>}
        <button type="button" className="secondary" onClick={load}>刷新审计</button>
      </div>

      {replay && (
        <div className="replay-panel">
          <h3>重放：{replay.封口事件?.event_id}</h3>
          <div className="chip-row">
            <span className={`chip ${replay.一致性?.no_drift ? 'ok' : 'locked'}`}>
              {replay.一致性?.no_drift ? '一致：封口态与写闸未拧花' : '发现拧花'}
            </span>
            <span className="chip read">事件件数 {replay.一致性?.event_count}</span>
            <span className="chip read">冻结件数 {replay.一致性?.bag_sealed_count}</span>
            <span className="chip read">当前件数 {replay.一致性?.current_count}</span>
          </div>
          <div className="chip-row">
            <span className="chip locked">再入袋：{replay.写闸?.再入袋}</span>
            <span className="chip locked">回流：{replay.写闸?.回流}</span>
            <span className="chip locked">改路由：{replay.写闸?.改路由}</span>
            <span className="chip read">封袋页/扫描册/口门/总览：只读开放</span>
          </div>
          <details open>
            <summary>完整重放 JSON</summary>
            <pre>{JSON.stringify(replay, null, 2)}</pre>
          </details>
        </div>
      )}

      <h3>审计事件流</h3>
      <table>
        <thead><tr><th>审计ID</th><th>时间</th><th>类型</th><th>引用</th><th>动作</th><th>事件编号</th><th>备注</th></tr></thead>
        <tbody>
          {rows.map((a) => (
            <tr key={a.id} className={a.event_id ? 'event-row' : ''}>
              <td>{a.id}</td><td>{a.created_at}</td><td>{a.kind}</td><td>{a.ref_id ?? '-'}</td>
              <td>{a.action}</td>
              <td className="event-cell">
                {a.event_id ? (
                  <button type="button" className="link-button" onClick={() => runReplay(a.event_id)}>
                    <code>{a.event_id}</code>
                  </button>
                ) : '-'}
              </td>
              <td>{a.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
