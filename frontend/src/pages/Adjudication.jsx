import { useEffect, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'

const VOTE_OPTIONS = [
  ['confirmed_event', '已裁定事件（SAE）'],
  ['non_event', '非事件'],
  ['competing_termination', '竞争终止（死亡）'],
  ['insufficient', '信息不足'],
]
const TAG = { pending: 'bad', in_review: 'warn', resolved: 'ok' }

export default function Adjudication() {
  const { user } = useAuth()
  const [queue, setQueue] = useState([])
  const [active, setActive] = useState(null)
  const [vote, setVote] = useState('confirmed_event')
  const [eventDate, setEventDate] = useState('')
  const [comment, setComment] = useState('')
  const [msg, setMsg] = useState('')
  const isChief = user.username === 'chief'

  async function reload() {
    setQueue(await api.get('/api/events/adjudication/queue'))
  }
  useEffect(() => { reload().catch((e) => setMsg(e.message)) }, [])

  async function openTask(t) {
    const detail = await api.get(`/api/events/adjudication/tasks/${t.id}`)
    setActive(detail)
    setVote(detail.my_vote || 'confirmed_event')
    setEventDate(detail.resolved_event_date || '')
    setComment('')
    setMsg('')
  }

  async function submitVote() {
    setMsg('')
    try {
      const t = await api.post(`/api/events/adjudication/tasks/${active.id}/vote`, {
        vote, proposed_event_date: eventDate || null, comment: comment || null,
      })
      setActive(t)
      setMsg(t.status === 'resolved'
        ? `专家组达成一致，任务已裁定为「${t.resolution_label}」`
        : t.disagreement ? '已记录你的投票；专家组意见分歧，等待首席终裁' : '投票已记录，等待其他专家')
      await reload()
    } catch (e) { setMsg(e.message) }
  }

  async function tieBreak() {
    setMsg('')
    try {
      const t = await api.post(`/api/events/adjudication/tasks/${active.id}/tie-break`, {
        final_vote: vote, event_date: eventDate || null,
        known_date: null, comment: comment || '首席终裁',
      })
      setActive(t)
      setMsg(`首席终裁完成：${t.resolution_label}`)
      await reload()
    } catch (e) { setMsg(e.message) }
  }

  return (
    <>
      <div className="panel">
        <h2>盲法事件裁定队列{isChief && <span className="tag info" style={{ marginLeft: 10 }}>首席裁定专家（可终裁分歧）</span>}</h2>
        {msg && <div className="alert">{msg}</div>}
        <p className="small muted">
          队列仅包含规则无法自动裁定的冲突候选。投票提交前各专家互不可见彼此意见；两人一致即结案，分歧则进入首席终裁。
        </p>
        <table className="grid">
          <thead><tr><th>任务</th><th>去重簇</th><th>中心/患者</th><th>规则建议</th><th>状态</th><th>我的投票</th><th></th></tr></thead>
          <tbody>
            {queue.map((t) => (
              <tr key={t.id}>
                <td className="mono">#{t.id}</td>
                <td className="mono small">{t.cluster_key}</td>
                <td className="small">{t.center_codes.join('、')} / {t.patient_ids.join(', ')}</td>
                <td className="small">{t.rule_classification_label}</td>
                <td>
                  <span className={`tag ${TAG[t.status] || ''}`}>
                    {t.status}{t.disagreement ? '（分歧）' : ''}
                  </span>
                </td>
                <td className="small">{t.my_vote || '未投'}</td>
                <td><button className="link" onClick={() => openTask(t)}>查看/裁定</button></td>
              </tr>
            ))}
            {queue.length === 0 && <tr><td colSpan={7} className="muted">暂无待人工裁定的冲突候选（规则可决的已自动结案）。</td></tr>}
          </tbody>
        </table>
      </div>

      {active && (
        <div className="panel">
          <h2>任务 #{active.id}：{active.cluster_key}</h2>
          <h3>候选证据（去重簇内 {active.evidence.length} 条）</h3>
          <table className="grid">
            <thead><tr><th>候选 ID</th><th>中心</th><th>类型/来源</th><th>发生日</th><th>获知日</th><th>严重度</th><th>外院/回执</th><th>证据/备注</th></tr></thead>
            <tbody>
              {active.evidence.map((e) => (
                <tr key={e.id}>
                  <td className="mono small">{e.candidate_id}</td>
                  <td className="mono">{e.center_code}</td>
                  <td className="small">{e.event_type}/{e.source_kind}{e.is_fatal ? '/死亡' : ''}</td>
                  <td className="mono small">{e.occurrence_date || '—'}</td>
                  <td className="mono small">{e.known_date || '—'}</td>
                  <td className="small">{e.severity || '—'}</td>
                  <td className="small">{e.external_receipt_id || '—'}</td>
                  <td className="small">{e.evidence}{e.notes ? `（${e.notes}）` : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>投票情况</h3>
          {active.status === 'resolved' ? (
            <table className="grid">
              <thead><tr><th>专家</th><th>意见</th><th>提议事件日</th><th>备注</th></tr></thead>
              <tbody>
                {active.votes.map((v) => (
                  <tr key={v.reviewer_id}>
                    <td>{v.reviewer}</td><td>{v.vote_label}</td>
                    <td className="mono small">{v.proposed_event_date || '—'}</td>
                    <td className="small">{v.comment || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="small muted">盲法进行中：已收到 {active.vote_count} 票，他人意见暂不可见 {active.votes.map((v) => `#${v.reviewer_id}`).join(' ')}。</p>
          )}

          {active.status !== 'resolved' && (
            <div className="subpanel">
              <h3>{isChief && active.status === 'in_review' ? '首席终裁' : '提交盲法投票'}</h3>
              <div className="row">
                <label className="field">
                  裁定结论
                  <select value={vote} onChange={(e) => setVote(e.target.value)}>
                    {VOTE_OPTIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
                  </select>
                </label>
                <label className="field">
                  提议事件日（最早满足阈值记录）
                  <input type="date" value={eventDate} onChange={(e) => setEventDate(e.target.value)} />
                </label>
              </div>
              <label className="field">
                意见
                <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
              </label>
              {isChief && active.status === 'in_review' ? (
                <button className="danger" onClick={tieBreak}>首席终裁并结案</button>
              ) : (
                <button onClick={submitVote} disabled={active.status === 'in_review' && !isChief}>
                  提交投票
                </button>
              )}
              {active.status === 'in_review' && !isChief && (
                <p className="small muted">专家组已分歧，仅首席裁定专家可终裁。</p>
              )}
            </div>
          )}
        </div>
      )}
    </>
  )
}
