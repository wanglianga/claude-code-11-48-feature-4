import { useEffect, useState } from 'react'
import { api } from '../api'

const STATUS_TAG = { locked: 'ok', registered: 'warn', superseded: '' }

export default function Admin() {
  const [centers, setCenters] = useState([])
  const [batches, setBatches] = useState([])
  const [dicts, setDicts] = useState([])
  const [mappings, setMappings] = useState([])
  const [labels, setLabels] = useState({})
  const [msg, setMsg] = useState('')

  async function reload() {
    const [c, b, d, m] = await Promise.all([
      api.get('/api/admin/centers'),
      api.get('/api/admin/batches'),
      api.get('/api/admin/dictionaries'),
      api.get('/api/admin/mappings'),
    ])
    setCenters(c)
    setBatches(b)
    setDicts(d)
    setMappings(m)
  }

  useEffect(() => {
    reload().catch((e) => setMsg(e.message))
  }, [])

  async function sync(code) {
    setMsg('')
    try {
      const label = labels[code] || '2026Q4'
      const b = await api.post(`/api/admin/centers/${code}/batches/sync`, { label })
      setMsg(`已登记 ${code} 新批次 #${b.id}（${b.record_counts.patients} 例），待锁定`)
      await reload()
    } catch (e) {
      setMsg(`同步失败：${e.message}`)
    }
  }

  async function lock(id) {
    setMsg('')
    try {
      await api.post(`/api/admin/batches/${id}/lock`)
      setMsg(`批次 #${id} 已锁定；该中心旧批次转为 superseded，进行中的运行不受影响`)
      await reload()
    } catch (e) {
      setMsg(`锁定失败：${e.message}`)
    }
  }

  return (
    <>
      <div className="panel">
        <h2>中心与锁定批次</h2>
        {msg && <div className="alert">{msg}</div>}
        <table className="grid">
          <thead>
            <tr><th>中心</th><th>名称</th><th>当前锁定批次</th><th>例数</th><th>可用范围</th><th>登记新批次</th></tr>
          </thead>
          <tbody>
            {centers.map((c) => (
              <tr key={c.code}>
                <td className="mono">{c.code}</td>
                <td>{c.name}</td>
                <td>{c.locked_batch ? `#${c.locked_batch.id}（${c.locked_batch.label}）` : '—'}</td>
                <td>{c.locked_batch?.record_counts?.patients ?? '—'}</td>
                <td className="small muted">
                  诊断年份：{c.locked_batch?.available_scope?.dx_years?.join(', ') || '—'}
                </td>
                <td>
                  <div className="row">
                    <input
                      style={{ width: 90 }}
                      placeholder="批次标签"
                      value={labels[c.code] || ''}
                      onChange={(e) => setLabels({ ...labels, [c.code]: e.target.value })}
                    />
                    <button className="secondary" onClick={() => sync(c.code)}>从中心同步</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h2>数据批次</h2>
        <table className="grid">
          <thead>
            <tr><th>ID</th><th>中心</th><th>标签</th><th>状态</th><th>例数</th><th>登记时间</th><th>锁定时间</th><th>操作</th></tr>
          </thead>
          <tbody>
            {batches.map((b) => (
              <tr key={b.id}>
                <td className="mono">#{b.id}</td>
                <td>{b.center_code}</td>
                <td>{b.label}</td>
                <td><span className={`tag ${STATUS_TAG[b.status] || ''}`}>{b.status}</span></td>
                <td>{b.record_counts?.patients}</td>
                <td className="small">{b.created_at?.slice(0, 19).replace('T', ' ')}</td>
                <td className="small">{b.locked_at?.slice(0, 19).replace('T', ' ') || '—'}</td>
                <td>
                  {b.status === 'registered' && (
                    <button className="secondary" onClick={() => lock(b.id)}>锁定</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h2>变量字典</h2>
        {dicts.map((d) => (
          <div key={d.id}>
            <h3>版本 {d.version}（{d.status}）</h3>
            <table className="grid">
              <thead><tr><th>变量</th><th>实体</th><th>中文名</th><th>类型</th><th>允许值</th></tr></thead>
              <tbody>
                {d.variables.map((v) => (
                  <tr key={v.name}>
                    <td className="mono">{v.name}</td>
                    <td>{v.entity}</td>
                    <td>{v.label}</td>
                    <td>{v.value_type}</td>
                    <td className="small">{v.allowed_values?.join(', ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>

      <div className="panel">
        <h2>中心变量映射</h2>
        <table className="grid">
          <thead><tr><th>中心</th><th>标准变量</th><th>本地字段</th><th>值映射</th><th>状态</th></tr></thead>
          <tbody>
            {mappings.map((m, i) => (
              <tr key={i}>
                <td className="mono">{m.center_code}</td>
                <td className="mono">{m.variable}</td>
                <td className="mono">{m.local_field || '—'}</td>
                <td className="small mono">{m.value_map ? JSON.stringify(m.value_map) : '—'}</td>
                <td><span className={`tag ${m.status === 'mapped' ? 'ok' : 'bad'}`}>{m.status}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
