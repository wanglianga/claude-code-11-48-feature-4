import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import ResultTable from '../components/ResultTable'

const CENTER_STATUS = {
  success: ['成功', 'ok'], failed: ['失败', 'bad'],
  missing_mapping: ['缺变量映射', 'bad'], pending: ['等待', ''],
}

export default function RunDetail() {
  const { id } = useParams()
  const { user } = useAuth()
  const [run, setRun] = useState(null)
  const [outputs, setOutputs] = useState([])
  const [title, setTitle] = useState('')
  const [msg, setMsg] = useState('')

  async function reload() {
    const r = await api.get(`/api/runs/${id}`)
    setRun(r)
    const os = await api.get('/api/outputs')
    setOutputs(os.filter((o) => o.run_id === r.id))
  }
  useEffect(() => {
    reload().catch((e) => setMsg(e.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  async function createOutput() {
    setMsg('')
    try {
      const o = await api.post(`/api/runs/${id}/outputs`, { title: title || `运行 #${id} 输出表` })
      setMsg(`输出表 #${o.id} 已提交审查（${o.flag_count} 处小样本标记）`)
      await reload()
    } catch (e) {
      setMsg(`创建输出失败：${e.message}`)
    }
  }

  if (!run) return <div className="panel">加载中…</div>
  const cov = run.result.coverage

  return (
    <>
      <div className="panel">
        <h2>运行 #{run.id}</h2>
        {msg && <div className="alert">{msg}</div>}
        <p className="small muted">
          规则 v{run.rule_version_no} · 条件哈希 <span className="mono">{run.conditions_hash?.slice(0, 16)}…</span>{' '}
          · 请求哈希 <span className="mono">{run.request_hash?.slice(0, 16)}…</span> · k={run.k_threshold}
        </p>
        <h3>队列条件（启动时锁定）</h3>
        <pre className="json">{JSON.stringify(run.conditions, null, 2)}</pre>
        <h3>批次快照（运行启动时锁定，后续批次替换不影响本运行）</h3>
        <table className="grid">
          <thead><tr><th>中心</th><th>批次</th><th>执行状态</th><th>例数</th><th>耗时</th><th>说明</th></tr></thead>
          <tbody>
            {run.center_results.map((c) => {
              const [label, cls] = CENTER_STATUS[c.status] || [c.status, '']
              return (
                <tr key={c.center_code}>
                  <td>{c.center_code} · {c.center_name}</td>
                  <td className="mono">#{c.batch_id}（{c.batch_label}）</td>
                  <td><span className={`tag ${cls}`}>{label}</span></td>
                  <td>{c.total_n ?? '—（未纳入，非零例）'}</td>
                  <td className="small">{c.duration_ms ?? '—'} ms</td>
                  <td className="small">{c.error || ''}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {run.adjudication && (
        <div className="panel">
          <h2>事件裁定与竞争风险口径（运行启动时冻结）</h2>
          <p className="small muted">
            裁定清单 <b className="mono">{run.adjudication.snapshot_version}</b>（规则 {run.adjudication.rule_version}）·
            时间基准：<b>{run.adjudication.time_basis_label}</b> ·
            待裁定候选：<b className={run.adjudication.pending_count ? 'tag bad' : ''}>
              {run.adjudication.pending_count}</b>
            （待裁定者不计为事件，单独列示；运行期间新完成的裁定只进入下一次运行）
          </p>
          <p className="small mono">清单哈希：{run.adjudication.snapshot_hash}</p>
          <h3>各中心事件确认覆盖</h3>
          <table className="grid">
            <thead><tr><th>中心</th><th>裁定簇数</th><th>确认事件</th><th>确认率</th></tr></thead>
            <tbody>
              {Object.entries(run.adjudication.center_confirmation_coverage).map(([code, v]) => (
                <tr key={code}>
                  <td className="mono">{code}</td><td>{v.clusters}</td><td>{v.confirmed}</td>
                  <td>{v.confirmation_rate === null ? '—' : `${(v.confirmation_rate * 100).toFixed(1)}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3>分类计数</h3>
          <p className="small mono">{JSON.stringify(run.adjudication.classification_counts)}</p>
        </div>
      )}

      <div className="panel">
        <h3>覆盖范围</h3>
        {cov.complete ? (
          <div className="alert ok">全部 6 家中心均成功纳入，无覆盖缺口。</div>
        ) : (
          <div className="alert bad">
            以下中心未纳入总计（缺口而非零例）：
            <ul>
              {cov.excluded_centers.map((e) => (
                <li key={e.center}><b>{e.center}</b>（{e.status}）：{e.reason}</li>
              ))}
            </ul>
          </div>
        )}
        <h3>聚合结果（仅汇总通过执行的中心统计量）</h3>
        <ResultTable table={run.result} />
        {user.role === 'researcher' && run.status.startsWith('completed') && (
          <div className="row" style={{ marginTop: 12 }}>
            <input
              className="grow"
              placeholder="输出表标题"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
            <button onClick={createOutput}>提交输出表审查</button>
          </div>
        )}
      </div>

      {outputs.length > 0 && (
        <div className="panel">
          <h3>本运行的输出表</h3>
          <table className="grid">
            <thead><tr><th>ID</th><th>标题</th><th>状态</th><th>小样本标记</th><th>创建时间</th></tr></thead>
            <tbody>
              {outputs.map((o) => (
                <tr key={o.id}>
                  <td className="mono">#{o.id}</td>
                  <td>{o.title}</td>
                  <td><span className={`tag ${o.status === 'approved' ? 'ok' : o.status === 'rejected' ? 'bad' : 'warn'}`}>{o.status}</span></td>
                  <td>{o.flag_count}</td>
                  <td className="small">{o.created_at?.slice(0, 19).replace('T', ' ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
