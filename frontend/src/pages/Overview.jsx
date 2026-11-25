import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useReloadOnChange } from '../hooks/useReloadOnChange'

const surface_panel = true
export default function Overview() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/总览').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  useReloadOnChange(load)

  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中…</div>
  const _ok = data && (data.surface_ok || data.surface_probe)
  const d = data.分布 || {}
  const _surf = _ok ? (data.surface_待路由 ?? d.待路由) : d.待路由
  const rec = data.对账 || {}
  return (
    <div className="panel">
      <h2>总览对账</h2>
      <p className="muted">当前班次：{data.班次?.business_day} {data.班次?.name} · {data.班次?.sealed ? '已封班' : '未封班'}</p>
      <div className="policy-banner sealed">
        全局唯一架构 A：<strong>{data.边界口径 || '袋体写边界全关'}</strong>。
        封袋确认后禁止再入袋、禁止回流拽出、禁止改路由；封袋页、扫描册、口门、总览与审计只读/重放仍开。
      </div>

      <div className="grid">
        {Object.entries(d).map(([k, v]) => (
          <div className="stat" key={k}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
      </div>

      <div className="grid">
        <div className="stat"><div className="n">{rec.袋数 ?? '-'}</div><div className="l">袋数</div></div>
        <div className="stat"><div className="n">{rec.已封袋数 ?? '-'}</div><div className="l">已封袋数</div></div>
        <div className="stat"><div className="n">{rec.封口冻结件数 ?? '-'}</div><div className="l">封口冻结件数</div></div>
        <div className="stat"><div className={`n ${rec.冻结件数一致 === false ? 'bad' : 'good'}`}>{rec.拧花袋数 ?? '-'}</div><div className="l">拧花袋数</div></div>
      </div>
      {rec.冻结件数一致 === false && (
        <div className="err">对账不一致：封口冻结 {rec.封口冻结件数} 件，已封袋扫描 {rec.已封袋扫描件数} 件；拧花袋 {JSON.stringify(rec.拧花袋)}</div>
      )}

      <h3>口门在口</h3>
      <table>
        <thead><tr><th>口门</th><th>标题</th><th>状态</th><th>在口件数</th><th>未入袋</th><th>集包中</th><th>已封袋</th><th>封口事件</th></tr></thead>
        <tbody>
          {(data.口门 || []).map((c) => (
            <tr key={c.id}>
              <td>{c.code}</td><td>{c.title}</td><td>{c.status}</td>
              <td>{c.在口件数}</td><td>{c.未入袋件数}</td><td>{c.集包中件数}</td><td>{c.已封袋件数}</td>
              <td className="event-cell"><code>{c.封口事件编号 || '-'}</code></td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>集包袋封条状态</h3>
      <table>
        <thead><tr><th>编号</th><th>袋码</th><th>口门</th><th>状态</th><th>当前件数</th><th>冻结件数</th><th>写闸</th><th>事件/重放</th></tr></thead>
        <tbody>
          {(data.封袋 || []).map((b) => (
            <tr key={b.id} className={b.sealed ? 'locked-row' : ''}>
              <td>{b.id}</td><td>{b.code}</td><td>{b.口门码}</td>
              <td><span className={`badge ${b.sealed ? 'badge-locked' : 'badge-open'}`}>{b.status}</span></td>
              <td>{b.件数}</td><td>{b.封口件数 ?? '-'}</td>
              <td>{b.sealed ? '再入袋/回流/改路由全关' : '开放'}</td>
              <td>
                {b.封口事件编号 ? (
                  <Link to={`/审计?event_id=${encodeURIComponent(b.封口事件编号)}`}>
                    <code>{b.封口事件编号}</code> 重放
                  </Link>
                ) : '-'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新总览</button></div>
    </div>
  )
}
