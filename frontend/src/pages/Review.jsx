import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'

export default function Review() {
  const [outputs, setOutputs] = useState([])
  const [ruleQueue, setRuleQueue] = useState([])
  const [msg, setMsg] = useState('')

  async function reload() {
    const [o, q] = await Promise.all([
      api.get('/api/outputs'),
      api.get('/api/rules/review/queue'),
    ])
    setOutputs(o)
    setRuleQueue(q)
  }
  useEffect(() => {
    reload().catch((e) => setMsg(e.message))
  }, [])

  async function reviewRule(v, action) {
    const comment = window.prompt(action === 'approve' ? '批准意见（可选）' : '拒绝原因')
    if (comment === null) return
    try {
      await api.post(`/api/rules/versions/${v.id}/${action}`, { comment })
      await reload()
    } catch (e) {
      setMsg(e.message)
    }
  }

  return (
    <>
      <div className="panel">
        <h2>待审规则版本</h2>
        {msg && <div className="alert bad">{msg}</div>}
        {ruleQueue.length === 0 ? <p className="muted">暂无待审规则。</p> : (
          <table className="grid">
            <thead><tr><th>规则</th><th>版本</th><th>条件数</th><th>提交人</th><th>提交时间</th><th>操作</th></tr></thead>
            <tbody>
              {ruleQueue.map((v) => (
                <tr key={v.id}>
                  <td>规则 #{v.rule_id}</td>
                  <td>v{v.version_no}</td>
                  <td>{v.conditions.length}</td>
                  <td>{v.created_by}</td>
                  <td className="small">{v.submitted_at?.slice(0, 19).replace('T', ' ')}</td>
                  <td>
                    <Link to={`/rules/${v.rule_id}`}>查看</Link>{' '}
                    <button className="link" onClick={() => reviewRule(v, 'approve')}>批准</button>
                    <button className="link" onClick={() => reviewRule(v, 'reject')}>拒绝</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <h2>输出表审查队列</h2>
        <table className="grid">
          <thead><tr><th>ID</th><th>标题</th><th>运行</th><th>状态</th><th>小样本标记</th><th>提交人</th><th></th></tr></thead>
          <tbody>
            {outputs.map((o) => (
              <tr key={o.id}>
                <td className="mono">#{o.id}</td>
                <td>{o.title}</td>
                <td className="mono">#{o.run_id}</td>
                <td>
                  <span className={`tag ${o.status === 'approved' ? 'ok' : o.status === 'rejected' ? 'bad' : 'warn'}`}>
                    {o.status}
                  </span>
                </td>
                <td>{o.flag_count > 0 ? <span className="tag bad">{o.flag_count}</span> : '0'}</td>
                <td>{o.created_by}</td>
                <td><Link to={`/review/${o.id}`}>审查</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
