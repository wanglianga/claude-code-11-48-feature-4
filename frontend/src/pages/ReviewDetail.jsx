import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api'
import ResultTable, { metricLabel } from '../components/ResultTable'

const REASON_LABEL = {
  small_count: '小计数值',
  small_group: '小分组（整组隐藏）',
  differencing: '可由差分推回',
}

export default function ReviewDetail() {
  const { id } = useParams()
  const [out, setOut] = useState(null)
  const [msg, setMsg] = useState(null)
  const [mergeSel, setMergeSel] = useState([])
  const [mergeLabel, setMergeLabel] = useState('合并组')
  const [comment, setComment] = useState('')

  async function reload() {
    setOut(await api.get(`/api/outputs/${id}`))
  }
  useEffect(() => {
    reload().catch((e) => setMsg({ type: 'bad', text: e.message }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  async function decide(action, detail = {}) {
    setMsg(null)
    try {
      const res = await api.post(`/api/outputs/${id}/decision`, { action, detail, comment })
      setOut(res)
      setMergeSel([])
      if (res.status === 'approved') {
        setMsg({ type: 'ok', text: `已批准并发布（报告 #${res.published_report_id}）` })
      } else if (res.flag_count > 0) {
        setMsg({ type: 'bad', text: `仍有 ${res.flag_count} 处标记未解决，输出留在审查队列` })
      }
    } catch (e) {
      setMsg({ type: 'bad', text: e.message })
    }
  }

  if (!out) return <div className="panel">加载中…</div>
  const pending = out.status === 'pending_review'
  const flaggedGroups = [...new Set(out.flags.map((f) => f.group).filter((g) => g !== '__total__'))]

  return (
    <>
      <div className="panel">
        <h2>审查输出 #{out.id}：{out.title}</h2>
        {msg && <div className={`alert ${msg.type === 'bad' ? 'bad' : 'ok'}`}>{msg.text}</div>}
        <p className="small muted">
          运行 #{out.run_id} · k={out.k_threshold} · 状态 <b>{out.status}</b>
        </p>
        <h3>结果表（红色 = 被标记单元格，灰斜体 = 已抑制）</h3>
        <ResultTable table={out.table_data} flags={out.flags} />
        <h3>标记清单（{out.flags.length}）</h3>
        {out.flags.length === 0 ? <p className="muted">无未解决标记。</p> : (
          <table className="grid">
            <thead><tr><th>分组</th><th>指标</th><th>值</th><th>原因</th></tr></thead>
            <tbody>
              {out.flags.map((f, i) => (
                <tr key={i}>
                  <td>{f.group}</td>
                  <td>{metricLabel(f.metric)}</td>
                  <td>{f.value ?? '—'}</td>
                  <td><span className="tag bad">{REASON_LABEL[f.reason] || f.reason}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {pending && (
        <div className="panel">
          <h3>审查决定</h3>
          <label className="field">
            审查意见
            <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
          </label>

          <h3>方式一：合并小分组</h3>
          <div className="row">
            {out.table_data.groups.map((g) => (
              <label key={g.label} className={flaggedGroups.includes(g.label) ? 'cell-flag' : ''}>
                <input
                  type="checkbox"
                  checked={mergeSel.includes(g.label)}
                  onChange={() => setMergeSel(mergeSel.includes(g.label)
                    ? mergeSel.filter((x) => x !== g.label) : [...mergeSel, g.label])}
                />{' '}
                {g.label}（n={g.n}）
              </label>
            ))}
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <input value={mergeLabel} onChange={(e) => setMergeLabel(e.target.value)}
              placeholder="合并后组名" />
            <button
              className="secondary"
              disabled={mergeSel.length < 2}
              onClick={() => decide('merge', { groups: mergeSel, into: mergeLabel })}
            >
              合并所选分组
            </button>
          </div>

          <h3>方式二：抑制被标记单元格</h3>
          <p className="small muted">
            将当前全部 {out.flags.length} 处被标记的单元格（含必要的合计）置为 ⊘。
            若某列只剩一个隐藏格，仍需连合计一起抑制以防差分推回。
          </p>
          <button
            className="secondary"
            disabled={out.flags.length === 0}
            onClick={() => decide('suppress', {
              cells: out.flags.map((f) => ({ group: f.group, metric: f.metric })),
            })}
          >
            抑制全部标记单元格
          </button>

          <h3>方式三：直接决定</h3>
          <div className="row">
            <button disabled={out.flags.length > 0} onClick={() => decide('approve')}>
              批准并发布
            </button>
            <button className="danger" onClick={() => decide('reject')}>拒绝交付</button>
          </div>
          {out.flags.length > 0 && (
            <p className="small muted">存在未解决标记时不能批准；请先合并或抑制。</p>
          )}
        </div>
      )}

      <div className="panel">
        <h3>审查记录</h3>
        {out.decisions.length === 0 ? <p className="muted">暂无。</p> : (
          <table className="grid">
            <thead><tr><th>时间</th><th>审查员</th><th>动作</th><th>细节</th><th>意见</th></tr></thead>
            <tbody>
              {out.decisions.map((d) => (
                <tr key={d.id}>
                  <td className="small">{d.decided_at.slice(0, 19).replace('T', ' ')}</td>
                  <td>{d.reviewer}</td>
                  <td><span className="tag info">{d.action}</span></td>
                  <td className="mono small">{JSON.stringify(d.detail)}</td>
                  <td className="small">{d.comment || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
