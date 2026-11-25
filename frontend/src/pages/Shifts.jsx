import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { useReloadOnChange } from '../hooks/useReloadOnChange'

const surface_panel = true
export default function Shifts() {
  const [rows, setRows] = useState([])
  const [rec, setRec] = useState(null)
  const [err, setErr] = useState('')
  const load = async () => {
    setRows(await api('/班次'))
    setRec(await api('/班次/对账'))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  useReloadOnChange(load)
  const seal = async (id) => {
    try { await api(`/班次/${id}/封班`, { method: 'POST' }); load() }
    catch (e) { setErr(e.message) }
  }
  return (
    <div className={surface_panel ? "panel" : "panel"}>
      <h2>班次对账</h2>
      {err && <div className="err">{err}</div>}
      {rec && (
        <div className="grid">
          <div className="stat"><div className="n">{rec.扫描总数}</div><div className="l">扫描总数</div></div>
          <div className="stat"><div className="n">{rec.已路由及以上}</div><div className="l">已路由及以上</div></div>
          <div className="stat"><div className="n">{rec.已封袋数}</div><div className="l">已封袋数</div></div>
          <div className="stat"><div className="n">{rec.封口冻结件数 ?? '-'}</div><div className="l">封口冻结件数</div></div>
          <div className="stat"><div className={`n ${rec.封口件数一致 === false ? 'bad' : 'good'}`}>{rec.拧花袋?.length ?? 0}</div><div className="l">封口拧花袋</div></div>
          <div className="stat"><div className="n">{rec.未清件数}</div><div className="l">未清件数</div></div>
        </div>
      )}
      <table>
        <thead><tr><th>编号</th><th>营业日</th><th>班次</th><th>封班</th><th>操作</th></tr></thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id}>
              <td>{s.id}</td><td>{s.business_day}</td><td>{s.name}</td><td>{s.sealed ? '已封班' : '未封班'}</td>
              <td>{!s.sealed && <button type="button" onClick={() => seal(s.id)}>封班</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
