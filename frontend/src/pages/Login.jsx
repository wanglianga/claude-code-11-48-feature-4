import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'

const DEMO = [
  ['admin', 'admin123', '数据管理员'],
  ['researcher1', 'research123', '研究者·甲'],
  ['researcher2', 'research123', '研究者·乙'],
  ['reviewer', 'review123', '结果审查员'],
  ['adjudicator1', 'adjudicate123', '裁定专家·甲/乙'],
  ['chief', 'adjudicate123', '首席裁定专家'],
]

export default function Login() {
  const { login } = useAuth()
  const nav = useNavigate()
  const [username, setUsername] = useState('researcher1')
  const [password, setPassword] = useState('research123')
  const [error, setError] = useState('')

  async function submit(e) {
    e.preventDefault()
    setError('')
    try {
      const user = await login(username, password)
      const home = {
        data_admin: '/admin',
        reviewer: '/review',
        adjudicator: '/adjudication',
      }[user.role] || '/rules'
      nav(home)
    } catch (err) {
      setError(err.message || '登录失败')
    }
  }

  return (
    <div className="login-wrap">
      <div className="panel">
        <h2>受控分析平台登录</h2>
        <form onSubmit={submit}>
          <label className="field">
            用户名
            <input value={username} onChange={(e) => setUsername(e.target.value)} />
          </label>
          <label className="field">
            密码
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          {error && <div className="alert bad">{error}</div>}
          <button type="submit" style={{ width: '100%' }}>登录</button>
        </form>
        <h3 style={{ marginTop: 16 }}>演示账号</h3>
        <table className="grid">
          <tbody>
            {DEMO.map(([u, p, label]) => (
              <tr key={u}>
                <td>{label}</td>
                <td className="mono">{u}</td>
                <td className="mono">{p}</td>
                <td>
                  <button className="link" onClick={() => { setUsername(u); setPassword(p) }}>
                    填入
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
