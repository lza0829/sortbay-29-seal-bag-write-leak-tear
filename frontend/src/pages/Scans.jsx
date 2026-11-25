import React, { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { useReloadOnChange } from '../hooks/useReloadOnChange'

function RerouteCell({ scan, chutes, onReroute }) {
  const [target, setTarget] = useState('')
  const canAttempt = scan.封袋已封
    || scan.改路由允许
    || ['已路由', '已入袋', '已封袋'].includes(scan.status)
  if (!canAttempt) return <span className="muted">-</span>
  return (
    <span className="inline-actions">
      <select value={target} onChange={(e) => setTarget(e.target.value)} aria-label={`扫描${scan.id}目标口门`}>
        <option value="">改到口门</option>
        {chutes.map((c) => (
          <option key={c.id} value={c.id}>{c.code}</option>
        ))}
      </select>
      <button
        type="button"
        className="secondary"
        disabled={!target && !scan.封袋已封}
        title={scan.封袋已封 ? '可不选目标直接点击：后端封口闸会先拒绝该改路由写口' : ''}
        onClick={() => onReroute(scan.id, target ? Number(target) : 0)}
      >
        改路由
      </button>
    </span>
  )
}

const surface_panel = true
export default function Scans() {
  const [rows, setRows] = useState([])
  const [lines, setLines] = useState([])
  const [chutes, setChutes] = useState([])
  const [shift, setShift] = useState(null)
  const [barcode, setBarcode] = useState('')
  const [dest, setDest] = useState('110')
  const [lineId, setLineId] = useState('')
  const [keyword, setKeyword] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [searchParams] = useSearchParams()
  const eventFilter = searchParams.get('event_id') || ''

  const load = async () => {
    const [scans, ls, cs, shifts] = await Promise.all([
      api('/扫描'), api('/分拣线'), api('/口门'), api('/班次'),
    ])
    setRows(scans)
    setLines(ls)
    setChutes(cs)
    setShift(shifts.find((s) => !s.sealed) || shifts[0] || null)
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  useReloadOnChange(load)

  const visibleRows = useMemo(() => rows.filter((r) => {
    const kw = keyword.trim()
    const matchKeyword = !kw || r.barcode.includes(kw) || String(r.id).includes(kw) || (r.dest_code || '').includes(kw)
    const matchEvent = !eventFilter || r.封口事件编号 === eventFilter
    return matchKeyword && matchEvent
  }), [rows, keyword, eventFilter])

  const call = async (work, ok) => {
    setErr(''); setMsg('')
    try {
      await work()
      setMsg(ok)
      await load()
      return true
    } catch (e) {
      setErr(e.message)
      return false
    }
  }

  const create = () => call(
    () => api('/扫描', {
      method: 'POST',
      body: JSON.stringify({
        条码: barcode,
        目的地码: dest,
        分拣线编号: lineId ? Number(lineId) : undefined,
      }),
    }),
    '过机装载成功，扫描册/班次入口已同刷',
  ).then((ok) => { if (ok) setBarcode('') })

  const routeOne = (id) => call(() => api(`/扫描/${id}/路由`, { method: 'POST' }), '路由裁决成功')
  const divertOne = (id) => call(
    () => api('/回流批次', { method: 'POST', body: JSON.stringify({ 扫描编号: id }) }),
    '已建回流批次',
  )
  const rerouteOne = (id, chuteId) => call(
    () => api(`/扫描/${id}/改路由`, { method: 'POST', body: JSON.stringify({ 口门编号: chuteId }) }),
    '改路由成功',
  )

  return (
    <div className={surface_panel ? "panel" : "panel"}>
      <h2>扫描册 · 过机扫描装载</h2>
      <p className="muted">
        架构 A · 袋体写边界全关：封袋条码仍可在扫描册只读查看，并保留回流/改路由尝试入口；后端会按同一封口事件拒绝写操作。
        当前班次入口：{shift ? `${shift.business_day} ${shift.name}（${shift.sealed ? '已封班' : '未封班'}）` : '加载中'}
        ；装载时由后端绑定当前未封班与分拣线。
      </p>
      {eventFilter && <div className="msg">正在高亮封口事件 <code>{eventFilter}</code> 的袋内条码</div>}
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}
      <div className="row">
        <label>条码<input value={barcode} onChange={(e) => setBarcode(e.target.value)} /></label>
        <label>目的地码<input value={dest} onChange={(e) => setDest(e.target.value)} /></label>
        <label>分拣线
          <select value={lineId} onChange={(e) => setLineId(e.target.value)}>
            <option value="">默认启用线</option>
            {lines.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        </label>
        <button type="button" onClick={create}>装载扫描</button>
        <label>册内搜索<input value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="条码/编号/目的地" /></label>
      </div>
      <table>
        <thead>
          <tr>
            <th>编号</th><th>条码</th><th>目的地</th><th>状态</th><th>口门</th><th>集包袋</th><th>封口事件</th><th>写操作</th>
          </tr>
        </thead>
        <tbody>
          {visibleRows.map((r) => (
            <tr key={r.id} className={r.封袋已封 ? 'locked-row' : ''}>
              <td>{r.id}</td>
              <td><strong>{r.barcode}</strong></td>
              <td>{r.dest_code}</td>
              <td>
                <span className={`badge ${r.半截 ? 'badge-torn' : r.封袋已封 ? 'badge-locked' : ''}`}>{r.status}</span>
                {r.半截 && <div className="warn-text">封条半截</div>}
              </td>
              <td>{r.口门码 || r.chute_id || '-'}</td>
              <td>{r.袋码 || r.bag_id || '-'}</td>
              <td className="event-cell">
                {r.封口事件编号 ? (
                  <Link to={`/审计?event_id=${encodeURIComponent(r.封口事件编号)}`}>
                    <code>{r.封口事件编号}</code>
                  </Link>
                ) : '-'}
                {r.半截 && <div className="warn-text">{(r.缺口 || []).join('；')}</div>}
              </td>
              <td>
                <span className="inline-actions">
                  {(r.status === '待路由' || r.status === '错分回流') && (
                    <button type="button" onClick={() => routeOne(r.id)} disabled={!r.路由允许}>路由</button>
                  )}
                  {(r.回流允许 || r.封袋已封 || ['已路由', '已入袋'].includes(r.status)) && (
                    <button
                      type="button"
                      className="secondary"
                      title={r.封袋已封 ? '封口写边界已全关；点击将由后端拒绝并提示' : ''}
                      onClick={() => divertOne(r.id)}
                    >
                      回流
                    </button>
                  )}
                  <RerouteCell scan={r} chutes={chutes} onReroute={rerouteOne} />
                </span>
              </td>
            </tr>
          ))}
          {!visibleRows.length && <tr><td colSpan={8} className="muted">扫描册暂无匹配记录</td></tr>}
        </tbody>
      </table>
    </div>
  )
}
