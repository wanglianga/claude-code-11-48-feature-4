import { Navigate, NavLink, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth'
import Login from './pages/Login'
import Admin from './pages/Admin'
import Rules from './pages/Rules'
import RuleEditor from './pages/RuleEditor'
import Runs from './pages/Runs'
import RunDetail from './pages/RunDetail'
import Compare from './pages/Compare'
import Review from './pages/Review'
import ReviewDetail from './pages/ReviewDetail'
import Reports from './pages/Reports'
import Events from './pages/Events'
import Adjudication from './pages/Adjudication'

const ROLE_HOME = {
  data_admin: '/admin',
  researcher: '/rules',
  reviewer: '/review',
  adjudicator: '/adjudication',
}

function Nav() {
  const { user, logout } = useAuth()
  if (!user) return null
  const links = []
  if (user.role === 'data_admin') {
    links.push(['/admin', '数据与批次'])
    links.push(['/events', '事件裁定'])
  }
  if (user.role === 'researcher') {
    links.push(['/rules', '队列规则'])
    links.push(['/events', '事件裁定'])
    links.push(['/runs', '运行'])
    links.push(['/compare', '运行对比'])
  }
  if (user.role === 'reviewer') links.push(['/review', '审查队列'])
  if (user.role === 'adjudicator') links.push(['/adjudication', '盲法裁定'])
  links.push(['/reports', '已发布报告'])
  return (
    <nav className="nav">
      <span className="brand">罕见病多中心受控分析平台</span>
      {links.map(([to, label]) => (
        <NavLink key={to} to={to} className={({ isActive }) => (isActive ? 'active' : '')}>
          {label}
        </NavLink>
      ))}
      <span className="spacer" />
      <span className="user">
        {user.display_name}（{user.role}）
      </span>
      <button className="link" onClick={logout}>退出</button>
    </nav>
  )
}

function Guard({ children }) {
  const { user } = useAuth()
  if (!user) return <Navigate to="/login" replace />
  return children
}

export default function App() {
  const { user } = useAuth()
  return (
    <>
      <Nav />
      <main className="main">
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/admin" element={<Guard><Admin /></Guard>} />
          <Route path="/rules" element={<Guard><Rules /></Guard>} />
          <Route path="/rules/:id" element={<Guard><RuleEditor /></Guard>} />
          <Route path="/runs" element={<Guard><Runs /></Guard>} />
          <Route path="/runs/:id" element={<Guard><RunDetail /></Guard>} />
          <Route path="/events" element={<Guard><Events /></Guard>} />
          <Route path="/adjudication" element={<Guard><Adjudication /></Guard>} />
          <Route path="/compare" element={<Guard><Compare /></Guard>} />
          <Route path="/review" element={<Guard><Review /></Guard>} />
          <Route path="/review/:id" element={<Guard><ReviewDetail /></Guard>} />
          <Route path="/reports" element={<Guard><Reports /></Guard>} />
          <Route path="*" element={<Navigate to={user ? ROLE_HOME[user.role] || '/reports' : '/login'} replace />} />
        </Routes>
      </main>
    </>
  )
}
