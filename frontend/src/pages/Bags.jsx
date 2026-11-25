import React, { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useReloadOnChange } from '../hooks/useReloadOnChange'

const surface_panel = true
export default function Bags() {
  const [bags, setBags] = useState([])
  const [chutes, setChutes] = useState([])
  const [scans, setScans] = useState([])
  const [chuteId, setChuteId] = useState('')
  const [bagId, setBagId] = useState('')
  const [scanId, setScanId] = useState('')
  const [operator, setOperator] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [replay, setReplay] = useState(null)

  const load = async () => {
    const [nextBags, cs, ss] = await Promise.all([
      api('/封袋'),
      api('/口门'),
      api('/扫描'),
    ])
    setBags(nextBags)
    setChutes(cs)
    setScans(ss)
    setChuteId((old) => old || (cs[0] ? String(cs[0].id) : ''))
    setBagId((old) => {
      if (old && nextBags.some((b) => String(b.id) === String(old))) return old
      const open = nextBags.find((b) => !b.sealed) || nextBags[0]
      return open ? String(open.id) : old
    })
  }

  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  useReloadOnChange(load)

  const selectedBag = useMemo(
    () => bags.find((b) => String(b.id) === String(bagId)) || null,
    [bags, bagId],
  )
  const bagItems = useMemo(
    () => scans.filter((s) => String(s.bag_id) === String(bagId)),
    [scans, bagId],
  )

  const run = async (work, success) => {
    setErr(''); setMsg('')
    try {
      const data = await work()
      if (success) setMsg(success(data))
      await load()
      return data
    } catch (e) {
      setErr(e.message)
      return null
    }
  }

  const openBag = () => run(
    () => api('/封袋/开袋', {
      method: 'POST',
      body: JSON.stringify({ 口门编号: Number(chuteId), 备注: operator ? `操作人=${operator}` : '' }),
    }),
    (b) => {
      setBagId(String(b.id))
      return `已开袋 ${b.code}`
    },
  )

  const add = () => run(
    () => api(`/封袋/${bagId}/入袋`, {
      method: 'POST',
      body: JSON.stringify({ 扫描编号: Number(scanId) }),
    }),
    () => {
      setScanId('')
      return '入袋成功，袋内件数已同刷'
    },
  )

  const seal = async () => {
    const b = await run(
      () => api(`/封袋/${bagId}/封袋确认`, { method: 'POST' }),
      (x) => `封袋确认成功：${x.code}，封口事件 ${x.封口事件编号}`,
    )
    if (b?.封口事件编号) {
      const data = await api(`/封袋/封口事件/${b.封口事件编号}/重放`).catch(() => null)
      if (data) setReplay(data)
    }
  }

  const showReplay = async (id = bagId) => {
    setErr(''); setMsg('')
    try {
      const data = await api(`/封袋/${id}/封口事件`)
      setReplay(data)
      setMsg('已由同一封口事件重放封袋页、扫描册、口门、总览与审计')
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className={surface_panel ? "panel" : "panel"}>
      <h2>封袋确认 · 集包袋册</h2>
      <p className="muted">
        当前全局唯一口径：<strong>已选架构 A · 袋体写边界全关</strong>。封条落下后，再入袋、回流、改路由三个写口全部拒绝；各册页与总览只读仍开。
      </p>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}

      <div className="row">
        <label>口门
          <select value={chuteId} onChange={(e) => setChuteId(e.target.value)}>
            {chutes.map((c) => <option key={c.id} value={c.id}>{c.code} {c.title}</option>)}
          </select>
        </label>
        <label>操作人<input value={operator} onChange={(e) => setOperator(e.target.value)} placeholder="可选" /></label>
        <button type="button" onClick={openBag}>开袋</button>
      </div>

      <div className="row">
        <label>袋编号
          <input value={bagId} onChange={(e) => setBagId(e.target.value)} placeholder="输入或在下表选择" />
        </label>
        <label>已路由扫描 / 扫描编号（列表保留不可入袋项，可验证后端拒绝）
          <select value={scanId} onChange={(e) => setScanId(e.target.value)}>
            <option value="">选择扫描</option>
            {scans.map((s) => (
              <option key={s.id} value={s.id}>
                {s.id} · {s.barcode} · {s.status}{s.口门码 ? ` · ${s.口门码}` : ''}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={add}
          disabled={!bagId || !scanId}
          title={selectedBag?.sealed ? '封口写边界已全关；点击将由后端拒绝并提示' : ''}
        >
          入袋
        </button>
        <button
          type="button"
          className="seal-button"
          onClick={seal}
          disabled={!bagId || selectedBag?.sealed}
        >
          封袋确认
        </button>
        <button type="button" className="secondary" onClick={() => showReplay()} disabled={!bagId}>
          重放封口事件
        </button>
      </div>

      {selectedBag && (
        <div className={`boundary-card ${selectedBag.sealed ? 'sealed' : 'open'}`}>
          <div className="boundary-title">
            <span className={`seal-dot ${selectedBag.sealed ? 'on' : ''}`} />
            {selectedBag.sealed ? '封条已亮 · 袋体写边界全关' : '袋口开放 · 未封，不得误挡入袋'}
          </div>
          <div className="chip-row">
            <span className={`chip ${selectedBag.add_allowed ? 'ok' : 'locked'}`}>再入袋：{selectedBag.add_allowed ? '开放' : '拒绝'}</span>
            <span className={`chip ${selectedBag.divert_allowed ? 'ok' : 'locked'}`}>回流：{selectedBag.divert_allowed ? '开放' : '拒绝'}</span>
            <span className={`chip ${selectedBag.reroute_allowed ? 'ok' : 'locked'}`}>改路由：{selectedBag.reroute_allowed ? '开放' : '拒绝'}</span>
            <span className="chip read">件数：{selectedBag.件数}{selectedBag.sealed ? ` / 封口冻结 ${selectedBag.封口件数}` : ''}</span>
            <span className={`chip ${selectedBag.件数一致 ? 'ok' : 'locked'}`}>件数{selectedBag.件数一致 ? '一致' : '拧花'}</span>
          </div>
          {selectedBag.封口事件编号 && (
            <div className="event-line">
              封口事件：<code>{selectedBag.封口事件编号}</code>
              {' '}
              <Link to={`/审计?event_id=${encodeURIComponent(selectedBag.封口事件编号)}`}>去审计重放</Link>
              {' · '}
              <Link to={`/扫描?event_id=${encodeURIComponent(selectedBag.封口事件编号)}`}>扫描册</Link>
              {' · '}
              <Link to="/口门">口门在口</Link>
              {' · '}
              <Link to="/">总览对账</Link>
            </div>
          )}
        </div>
      )}

      <h3>袋内件（{bagItems.length}）</h3>
      <table>
        <thead><tr><th>编号</th><th>条码</th><th>目的地</th><th>状态</th><th>口门</th><th>封口事件</th></tr></thead>
        <tbody>
          {bagItems.map((s) => (
            <tr key={s.id} className={s.封袋已封 ? 'locked-row' : ''}>
              <td>{s.id}</td><td>{s.barcode}</td><td>{s.dest_code}</td>
              <td>{s.status}{s.封袋已封 ? ' 🔒' : ''}</td><td>{s.口门码 || s.chute_id || '-'}</td>
              <td><code>{s.封口事件编号 || '-'}</code></td>
            </tr>
          ))}
          {!bagItems.length && <tr><td colSpan={6} className="muted">当前袋暂无件</td></tr>}
        </tbody>
      </table>

      <h3>集包袋册</h3>
      <table>
        <thead><tr><th>编号</th><th>袋码</th><th>口门</th><th>状态/封条</th><th>件数</th><th>写闸</th><th>封口事件</th><th>操作</th></tr></thead>
        <tbody>
          {bags.map((b) => (
            <tr key={b.id} className={b.sealed ? 'locked-row' : ''}>
              <td>{b.id}</td>
              <td>{b.code}</td>
              <td>{b.口门码}</td>
              <td><span className={`badge ${b.sealed ? 'badge-locked' : 'badge-open'}`}>{b.status}{b.sealed ? ' · 封条已亮' : ''}</span></td>
              <td>{b.件数}{b.sealed ? `（冻${b.封口件数}）` : ''}</td>
              <td>{b.sealed ? '入袋/回流/改路由全关' : '开放'}</td>
              <td className="event-cell"><code>{b.封口事件编号 || '-'}</code></td>
              <td>
                <button type="button" className="secondary" onClick={() => { setBagId(String(b.id)); setReplay(null) }}>选择</button>{' '}
                {b.封口事件编号 && <button type="button" onClick={() => showReplay(b.id)}>重放</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {replay && (
        <div className="replay-panel">
          <h3>同一封口事件重放结果</h3>
          <div className="chip-row">
            <span className={`chip ${replay.一致性?.no_drift ? 'ok' : 'locked'}`}>
              {replay.一致性?.no_drift ? '封口态/件数/写闸一致' : '重放发现拧花'}
            </span>
            <span className="chip read">事件件数 {replay.一致性?.event_count}</span>
            <span className="chip read">当前件数 {replay.一致性?.current_count}</span>
          </div>
          <details>
            <summary>查看完整重放 JSON（封袋页/扫描册/口门/总览/审计）</summary>
            <pre>{JSON.stringify(replay, null, 2)}</pre>
          </details>
        </div>
      )}
    </div>
  )
}
