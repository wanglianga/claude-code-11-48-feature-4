import { Fragment, useEffect, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'

const CENTERS = ['C1', 'C2', 'C3', 'C4', 'C5', 'C6']
const CLASS_TAG = {
  confirmed_event: 'ok',
  pending_adjudication: 'bad',
  non_event: '',
  competing_termination: 'warn',
  insufficient: 'warn',
  censored: 'info',
  indeterminate: 'warn',
}

export default function Events() {
  const { user } = useAuth()
  const [intakes, setIntakes] = useState([])
  const [clusters, setClusters] = useState([])
  const [snapshots, setSnapshots] = useState([])
  const [openCluster, setOpenCluster] = useState(null)
  const [openSnap, setOpenSnap] = useState(null)
  const [msg, setMsg] = useState('')

  const isAdmin = user.role === 'data_admin'
  const canFreeze = user.role === 'researcher' || isAdmin

  async function reload() {
    const [ins, cls, snaps] = await Promise.all([
      api.get('/api/events/intakes'),
      api.get('/api/events/clusters'),
      api.get('/api/events/snapshots'),
    ])
    setIntakes(ins)
    setClusters(cls)
    setSnapshots(snaps)
  }
  useEffect(() => { reload().catch((e) => setMsg(e.message)) }, [])

  async function intake(code) {
    setMsg('')
    try {
      const r = await api.post(`/api/events/intake/${code}`, { label: '2026Q3-events' })
      setMsg(`${code} 接入 ${r.candidate_count} 条候选（快照哈希 ${r.source_snapshot_hash.slice(0, 12)}…），已重建去重簇与 AR-v1 裁定`)
      await reload()
    } catch (e) { setMsg(`接入失败：${e.message}`) }
  }
  async function freeze() {
    setMsg('')
    try {
      const s = await api.post('/api/events/snapshots', {})
      setMsg(`已冻结不可变裁定清单 ${s.version}（待裁定 ${s.pending_count} 项）`)
      setOpenSnap(s.id)
      await reload()
    } catch (e) { setMsg(`冻结失败：${e.message}`) }
  }
  async function toggleCluster(id) {
    if (openCluster === id) { setOpenCluster(null); return }
    const detail = await api.get(`/api/events/clusters/${id}`)
    setOpenCluster(id)
    setClusters((prev) => prev.map((c) => (c.id === id ? { ...c, detail } : c)))
  }
  async function verifySnap(id) {
    const v = await api.get(`/api/events/snapshots/${id}/verify`)
    setMsg(v.intact ? `清单 #${id} 哈希校验一致，内容不可变` : `清单 #${id} 哈希不一致！`)
  }

  return (
    <>
      <div className="panel">
        <h2>事件候选接入（中心本地保存原始记录，受控区保存哈希审计副本）</h2>
        {msg && <div className="alert">{msg}</div>}
        <table className="grid">
          <thead><tr><th>中心</th><th>接入批次</th><th>候选数</th><th>原始快照哈希</th><th>状态</th>
            {isAdmin && <th>操作</th>}</tr></thead>
          <tbody>
            {CENTERS.map((code) => {
              const latest = intakes.filter((i) => i.center_code === code)[0]
              return (
                <tr key={code}>
                  <td className="mono">{code}</td>
                  <td>{latest ? latest.label : '—'}</td>
                  <td>{latest?.candidate_count ?? '—'}</td>
                  <td className="mono small">{latest ? `${latest.source_snapshot_hash.slice(0, 16)}…` : '—'}</td>
                  <td>{latest ? <span className="tag info">{latest.status}</span> : <span className="muted">未接入</span>}</td>
                  {isAdmin && (
                    <td><button className="secondary" onClick={() => intake(code)}>从中心接入/刷新</button></td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h2>实体去重与 AR-v1 裁定簇（{clusters.length}）</h2>
        <p className="small muted">
          同一临床事件跨院记录按显式关联字段/共用回执融合，仅计一次。点击行查看候选证据与裁定方式。
        </p>
        <table className="grid">
          <thead><tr><th>去重簇</th><th>涉及中心</th><th>患者</th><th>AR-v1 分类</th><th>事件日</th><th>获知日</th><th>方式/队列</th><th></th></tr></thead>
          <tbody>
            {clusters.map((cl) => (
              <Fragment key={cl.id}>
                <tr>
                  <td className="mono">{cl.cluster_key}</td>
                  <td>{cl.center_codes.join('、')}{cl.center_codes.length > 1 && <span className="tag warn" style={{ marginLeft: 6 }}>跨院</span>}</td>
                  <td className="small">{cl.patient_ids.join(', ')}</td>
                  <td><span className={`tag ${CLASS_TAG[cl.final_classification || cl.rule_classification] || ''}`}>
                    {cl.final_classification_label || cl.rule_classification_label}
                  </span></td>
                  <td className="mono small">{cl.final_event_date || cl.rule_event_date || '—'}</td>
                  <td className="mono small">{cl.final_known_date || cl.rule_known_date || '—'}</td>
                  <td className="small">{cl.mode === 'manual' ? `盲法人工（${cl.task_status}）` : '规则自动'}</td>
                  <td><button className="link" onClick={() => toggleCluster(cl.id)}>
                    {openCluster === cl.id ? '收起' : `证据(${cl.member_count})`}
                  </button></td>
                </tr>
                {openCluster === cl.id && cl.detail && (
                  <tr><td colSpan={8}>
                    <div className="subpanel">
                      <p className="small muted">去重依据：{cl.detail.dedup_reason}</p>
                      <table className="grid">
                        <thead><tr><th>候选 ID</th><th>类型/来源</th><th>发生日</th><th>获知日</th><th>严重度</th><th>外院/回执</th><th>证据</th></tr></thead>
                        <tbody>
                          {cl.detail.members.map((m) => (
                            <tr key={m.id}>
                              <td className="mono small">{m.candidate_id}</td>
                              <td className="small">{m.event_type}/{m.source_kind}{m.is_fatal ? '/死亡' : ''}</td>
                              <td className="mono small">{m.occurrence_date || '—'}</td>
                              <td className="mono small">{m.known_date || '—'}</td>
                              <td className="small">{m.severity || '—'}</td>
                              <td className="small">{m.external_receipt_id || '—'}</td>
                              <td className="small">{m.evidence}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {cl.detail.votes?.length > 0 && (
                        <p className="small">人工投票：{cl.detail.votes.map((v) => v.vote_label).join('、')}
                          {cl.detail.resolution_comment ? `；${cl.detail.resolution_comment}` : ''}
                        </p>
                      )}
                    </div>
                  </td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h2>不可变裁定清单（运行冻结引用） {canFreeze && <button onClick={freeze}>冻结当前裁定为新清单</button>}</h2>
        <table className="grid">
          <thead><tr><th>版本</th><th>规则</th><th>条目</th><th>待裁定</th><th>哈希</th><th>冻结时间</th><th>操作</th></tr></thead>
          <tbody>
            {snapshots.map((s) => (
              <tr key={s.id}>
                <td className="mono">{s.version}</td>
                <td>{s.rule_version}</td>
                <td>{s.item_count}</td>
                <td>{s.pending_count > 0 ? <span className="tag bad">{s.pending_count}</span> : '0'}</td>
                <td className="mono small">{s.snapshot_hash.slice(0, 16)}…</td>
                <td className="small">{s.created_at?.slice(0, 19).replace('T', ' ')}</td>
                <td>
                  <button className="link" onClick={() => setOpenSnap(openSnap === s.id ? null : s.id)}>
                    {openSnap === s.id ? '收起' : '覆盖/清单'}
                  </button>
                  <button className="link" onClick={() => verifySnap(s.id)}>校验</button>
                </td>
              </tr>
            ))}
            {snapshots.length === 0 && <tr><td colSpan={7} className="muted">尚未冻结裁定清单。</td></tr>}
          </tbody>
        </table>
        {openSnap && <SnapshotDetail id={openSnap} />}
      </div>
    </>
  )
}

function SnapshotDetail({ id }) {
  const [snap, setSnap] = useState(null)
  useEffect(() => { api.get(`/api/events/snapshots/${id}`).then(setSnap) }, [id])
  if (!snap) return <div className="subpanel">加载中…</div>
  return (
    <div className="subpanel">
      <h3>{snap.version} 各中心事件确认覆盖</h3>
      <table className="grid">
        <thead><tr><th>中心</th><th>裁定簇数</th><th>确认事件数</th><th>确认率</th></tr></thead>
        <tbody>
          {Object.entries(snap.manifest.center_confirmation_coverage).map(([code, v]) => (
            <tr key={code}>
              <td className="mono">{code}</td><td>{v.clusters}</td><td>{v.confirmed}</td>
              <td>{v.confirmation_rate === null ? '—' : `${(v.confirmation_rate * 100).toFixed(1)}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>分类计数</h3>
      <p className="small mono">{JSON.stringify(snap.manifest.classification_counts, null, 0)}</p>
      <h3>清单条目（{snap.items.length}）</h3>
      <table className="grid">
        <thead><tr><th>中心</th><th>患者</th><th>分类</th><th>事件日</th><th>获知日</th><th>方式</th></tr></thead>
        <tbody>
          {snap.items.map((i) => (
            <tr key={i.cluster_id}>
              <td className="mono">{i.center_code}</td>
              <td className="small">{i.patient_id}</td>
              <td><span className={`tag ${CLASS_TAG[i.classification] || ''}`}>{i.classification_label}</span></td>
              <td className="mono small">{i.event_date || '—'}</td>
              <td className="mono small">{i.known_date || '—'}</td>
              <td className="small">{i.mode === 'manual' ? '盲法人工' : '规则自动'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
