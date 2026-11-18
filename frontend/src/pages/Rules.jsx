import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'

const STATUS_LABEL = {
  draft: ['草稿', ''],
  in_review: ['审核中', 'warn'],
  approved: ['已批准', 'ok'],
  rejected: ['已拒绝', 'bad'],
}

export default function Rules() {
  const { user } = useAuth()
  const nav = useNavigate()
  const [rules, setRules] = useState([])
  const [name, setName] = useState('')
  const [msg, setMsg] = useState('')

  async function reload() {
    setRules(await api.get('/api/rules'))
  }
  useEffect(() => {
    reload().catch((e) => setMsg(e.message))
  }, [])

  async function create() {
    if (!name.trim()) return
    try {
      const rule = await api.post('/api/rules', { name: name.trim(), conditions: [] })
      nav(`/rules/${rule.id}`)
    } catch (e) {
      setMsg(e.message)
    }
  }

  return (
    <div className="panel">
      <h2>队列规则</h2>
      {msg && <div className="alert bad">{msg}</div>}
      {user.role === 'researcher' && (
        <div className="row" style={{ marginBottom: 12 }}>
          <input
            className="grow"
            placeholder="新规则名称，如：RD03 青少年队列"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button onClick={create}>新建规则</button>
        </div>
      )}
      <table className="grid">
        <thead>
          <tr><th>ID</th><th>名称</th><th>当前版本</th><th>状态</th><th></th></tr>
        </thead>
        <tbody>
          {rules.map((r) => {
            const [label, cls] = STATUS_LABEL[r.current_status] || [r.current_status, '']
            return (
              <tr key={r.id}>
                <td className="mono">#{r.id}</td>
                <td>{r.name}</td>
                <td>v{r.current_version_no ?? '—'}</td>
                <td><span className={`tag ${cls}`}>{label}</span></td>
                <td><Link to={`/rules/${r.id}`}>打开</Link></td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
