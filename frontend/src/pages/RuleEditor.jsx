import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'

const OPS = ['eq', 'ne', 'in', 'not_in', 'lt', 'le', 'gt', 'ge']
const STATUS_LABEL = {
  draft: ['草稿', ''], in_review: ['审核中', 'warn'],
  approved: ['已批准', 'ok'], rejected: ['已拒绝', 'bad'],
}

function newCid() {
  return `c-${Math.random().toString(36).slice(2, 9)}`
}

function parseValue(cond, variables) {
  const v = variables.find((x) => x.name === cond.variable)
  if (cond.op === 'in' || cond.op === 'not_in') {
    const arr = Array.isArray(cond.value) ? cond.value : String(cond.value).split(',').map((s) => s.trim()).filter(Boolean)
    return v?.value_type === 'numeric' ? arr.map(Number) : arr
  }
  if (v?.value_type === 'numeric') return Number(cond.value)
  return cond.value
}

function CondText({ c, variables }) {
  const v = variables.find((x) => x.name === c.variable)
  return (
    <span className="mono">
      [{c.kind === 'include' ? '纳入' : '排除'}] {v?.label || c.variable} {c.op}{' '}
      {Array.isArray(c.value) ? c.value.join(', ') : String(c.value)}
    </span>
  )
}

export default function RuleEditor() {
  const { id } = useParams()
  const { user } = useAuth()
  const [rule, setRule] = useState(null)
  const [variables, setVariables] = useState([])
  const [conditions, setConditions] = useState([])
  const [baseVersionId, setBaseVersionId] = useState(null)
  const [msg, setMsg] = useState(null)
  const [conflict, setConflict] = useState(null) // 409 concurrent_modification body
  const [restrictions, setRestrictions] = useState(null) // 409 restriction_removal body
  const [diffView, setDiffView] = useState(null)

  const reload = useCallback(async () => {
    const [r, d] = await Promise.all([
      api.get(`/api/rules/${id}`),
      api.get('/api/meta/dictionary'),
    ])
    setRule(r)
    setVariables(d.variables)
    const current = r.versions.find((v) => v.id === r.current_version_id)
    setConditions(current ? current.conditions.map((c) => ({ ...c })) : [])
    setBaseVersionId(r.current_version_id)
  }, [id])

  useEffect(() => {
    reload().catch((e) => setMsg({ type: 'bad', text: e.message }))
  }, [reload])

  async function save(extra = {}) {
    setMsg(null)
    try {
      const payload = {
        base_version_id: baseVersionId,
        conditions: conditions.map((c) => ({ ...c, value: parseValue(c, variables) })),
        ...extra,
      }
      const res = await api.put(`/api/rules/${id}/save`, payload)
      setConflict(null)
      setRestrictions(null)
      setRule(res.rule)
      const current = res.rule.versions.find((v) => v.id === res.rule.current_version_id)
      setConditions(current.conditions.map((c) => ({ ...c })))
      setBaseVersionId(res.rule.current_version_id)
      setMsg({ type: 'ok', text: res.unchanged ? '内容无变化' : `已保存为新版本 v${current.version_no}（草稿）` })
    } catch (e) {
      if (e.status === 409 && e.body?.error === 'concurrent_modification') {
        setConflict(e.body)
      } else if (e.status === 409 && e.body?.error === 'restriction_removal') {
        setRestrictions(e.body)
      } else {
        setMsg({ type: 'bad', text: e.message })
      }
    }
  }

  function acceptMerge() {
    setConditions(conflict.merged.map((c) => ({ ...c })))
    setBaseVersionId(conflict.current_version_id)
    setConflict(null)
    setMsg({ type: 'ok', text: '已载入自动合并结果，请检查后再次保存（将携带合并确认）' })
  }

  async function submit(versionId) {
    try {
      await api.post(`/api/rules/${id}/versions/${versionId}/submit`)
      setMsg({ type: 'ok', text: '已提交审核' })
      await reload()
    } catch (e) {
      setMsg({ type: 'bad', text: e.message })
    }
  }

  async function review(versionId, action) {
    const comment = window.prompt(action === 'approve' ? '批准意见（可选）' : '拒绝原因')
    if (comment === null) return
    try {
      await api.post(`/api/rules/versions/${versionId}/${action}`, { comment })
      await reload()
    } catch (e) {
      setMsg({ type: 'bad', text: e.message })
    }
  }

  async function showDiff(fromId, toId) {
    const d = await api.get(`/api/rules/${id}/diff?from_id=${fromId}&to_id=${toId}`)
    setDiffView({ fromId, toId, ...d })
  }

  function updateCond(i, patch) {
    setConditions(conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)))
  }

  if (!rule) return <div className="panel">加载中…</div>
  const current = rule.versions.find((v) => v.id === rule.current_version_id)
  const isResearcher = user.role === 'researcher'
  const isReviewer = user.role === 'reviewer'

  return (
    <>
      <div className="panel">
        <h2>规则：{rule.name}</h2>
        {msg && <div className={`alert ${msg.type === 'bad' ? 'bad' : 'ok'}`}>{msg.text}</div>}
        {rule.reviewed_version && (
          <div className="alert">
            最新进入审核的版本：v{rule.reviewed_version.version_no}（
            {STATUS_LABEL[rule.reviewed_version.status][0]}）。删除或放宽其中的排除条件时需要显式确认。
          </div>
        )}

        <h3>条件编辑（基于 v{current?.version_no}，草稿保存将生成新版本）</h3>
        {conditions.map((c, i) => (
          <div className={`cond ${c.kind}`} key={c.cid}>
            <span className="kind tag">{c.kind === 'include' ? '纳入' : '排除'}</span>
            <select
              value={c.kind}
              disabled={!isResearcher}
              onChange={(e) => updateCond(i, { kind: e.target.value })}
            >
              <option value="include">纳入</option>
              <option value="exclude">排除</option>
            </select>
            <select
              value={c.variable}
              disabled={!isResearcher}
              onChange={(e) => updateCond(i, { variable: e.target.value })}
            >
              {variables.map((v) => (
                <option key={v.name} value={v.name}>{v.label}（{v.name}）</option>
              ))}
            </select>
            <select
              value={c.op}
              disabled={!isResearcher}
              onChange={(e) => updateCond(i, { op: e.target.value })}
            >
              {OPS.map((op) => <option key={op} value={op}>{op}</option>)}
            </select>
            <input
              className="grow"
              value={Array.isArray(c.value) ? c.value.join(', ') : c.value}
              disabled={!isResearcher}
              placeholder={c.op.includes('in') ? '逗号分隔多个值' : '值'}
              onChange={(e) => updateCond(i, { value: e.target.value })}
            />
            {isResearcher && (
              <button className="link" onClick={() => setConditions(conditions.filter((_, j) => j !== i))}>
                删除
              </button>
            )}
          </div>
        ))}
        {isResearcher && (
          <div className="row">
            <button
              className="secondary"
              onClick={() => setConditions([...conditions,
                { cid: newCid(), kind: 'include', variable: variables[0]?.name || 'diagnosis.code', op: 'eq', value: '' }])}
            >
              + 添加条件
            </button>
            <button onClick={() => save()}>保存为新草稿版本</button>
            {current?.status === 'draft' && (
              <button className="secondary" onClick={() => submit(current.id)}>提交审核</button>
            )}
          </div>
        )}
      </div>

      {conflict && (
        <div className="modal-mask">
          <div className="modal">
            <h3>检测到并发修改</h3>
            <p className="muted">{conflict.message}</p>
            <div className="row" style={{ alignItems: 'flex-start' }}>
              <div className="diff-box grow">
                <h4>对方新增/修改（their_diff）</h4>
                {conflict.their_diff.added.map((c) => <div key={c.cid}>＋ <CondText c={c} variables={variables} /></div>)}
                {conflict.their_diff.removed.map((c) => <div key={c.cid}>－ <CondText c={c} variables={variables} /></div>)}
                {conflict.their_diff.changed.map((ch) => (
                  <div key={ch.cid}>～ <CondText c={ch.to} variables={variables} /></div>
                ))}
                {!conflict.their_diff.added.length && !conflict.their_diff.removed.length
                  && !conflict.their_diff.changed.length && <span className="muted">无</span>}
              </div>
              <div className="diff-box grow">
                <h4>你的修改（your_diff）</h4>
                {conflict.your_diff.added.map((c) => <div key={c.cid}>＋ <CondText c={c} variables={variables} /></div>)}
                {conflict.your_diff.removed.map((c) => <div key={c.cid}>－ <CondText c={c} variables={variables} /></div>)}
                {conflict.your_diff.changed.map((ch) => (
                  <div key={ch.cid}>～ <CondText c={ch.to} variables={variables} /></div>
                ))}
                {!conflict.your_diff.added.length && !conflict.your_diff.removed.length
                  && !conflict.your_diff.changed.length && <span className="muted">无</span>}
              </div>
            </div>
            {conflict.conflicts.length > 0 ? (
              <div className="alert bad">
                存在 {conflict.conflicts.length} 处冲突（同一条件被双方修改），请关闭本提示后基于最新版本手动调整。
              </div>
            ) : (
              <div className="alert ok">双方修改不冲突，可自动合并（{conflict.merged.length} 个条件）。</div>
            )}
            <div className="row">
              {conflict.merged && <button onClick={acceptMerge}>采用自动合并结果</button>}
              <button
                className="secondary"
                onClick={() => {
                  setBaseVersionId(conflict.current_version_id)
                  setConflict(null)
                }}
              >
                关闭（基于最新版本继续编辑）
              </button>
            </div>
          </div>
        </div>
      )}

      {restrictions && (
        <div className="modal-mask">
          <div className="modal">
            <h3>确认删除在审限制</h3>
            <p className="muted">{restrictions.message}</p>
            <div className="diff-box">
              {restrictions.removed_restrictions.map((r) => (
                <div key={r.cid}>
                  <span className="tag bad">{r.change === 'removed' ? '删除' : '修改'}</span>{' '}
                  <CondText c={r.condition} variables={variables} />
                </div>
              ))}
            </div>
            <p className="small muted">
              这些排除条件来自 v{restrictions.reviewed_version_no}（{restrictions.reviewed_status}）。
              确认后仍会保留原审核版本，本次修改另存为新草稿并需重新审核。
            </p>
            <div className="row">
              <button className="danger" onClick={() => { setRestrictions(null); save({ acknowledge_restriction_removal: true }) }}>
                确认删除并保存
              </button>
              <button className="secondary" onClick={() => setRestrictions(null)}>取消</button>
            </div>
          </div>
        </div>
      )}

      <div className="panel">
        <h3>版本历史</h3>
        <table className="grid">
          <thead>
            <tr><th>版本</th><th>状态</th><th>条件数</th><th>哈希</th><th>创建</th><th>审核意见</th><th>操作</th></tr>
          </thead>
          <tbody>
            {rule.versions.map((v) => {
              const [label, cls] = STATUS_LABEL[v.status] || [v.status, '']
              return (
                <tr key={v.id}>
                  <td>v{v.version_no}{v.id === rule.current_version_id ? '（当前）' : ''}</td>
                  <td><span className={`tag ${cls}`}>{label}</span></td>
                  <td>{v.conditions.length}</td>
                  <td className="mono small">{v.conditions_hash.slice(0, 12)}…</td>
                  <td className="small">{v.created_by} · {v.created_at?.slice(0, 16).replace('T', ' ')}</td>
                  <td className="small">{v.review_comment || '—'}</td>
                  <td className="small">
                    {isReviewer && v.status === 'in_review' && (
                      <>
                        <button className="link" onClick={() => review(v.id, 'approve')}>批准</button>
                        <button className="link" onClick={() => review(v.id, 'reject')}>拒绝</button>
                      </>
                    )}
                    {isResearcher && v.status === 'draft' && v.id === rule.current_version_id && (
                      <button className="link" onClick={() => submit(v.id)}>提交审核</button>
                    )}
                    {rule.versions.length > 1 && v.version_no > 1 && (
                      <button
                        className="link"
                        onClick={() => showDiff(
                          rule.versions.find((x) => x.version_no === v.version_no - 1)?.id, v.id,
                        )}
                      >
                        对比上一版
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {diffView && (
          <div className="diff-box">
            <h4>条件级差异（v…→ 所选版本）</h4>
            {diffView.added.map((c) => <div key={c.cid}>＋ <CondText c={c} variables={variables} /></div>)}
            {diffView.removed.map((c) => <div key={c.cid}>－ <CondText c={c} variables={variables} /></div>)}
            {diffView.changed.map((ch) => (
              <div key={ch.cid}>
                ～ <CondText c={ch.from} variables={variables} /> → <CondText c={ch.to} variables={variables} />
              </div>
            ))}
            {!diffView.added.length && !diffView.removed.length && !diffView.changed.length && (
              <span className="muted">两版条件一致</span>
            )}
            <div><button className="link" onClick={() => setDiffView(null)}>收起</button></div>
          </div>
        )}
      </div>
    </>
  )
}
