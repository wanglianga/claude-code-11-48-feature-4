import { createContext, useContext, useState } from 'react'
import { api, clearSession, getStoredUser, storeSession } from './api'

const AuthCtx = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(getStoredUser())

  async function login(username, password) {
    const res = await api.post('/api/auth/login', { username, password })
    storeSession(res.token, res.user)
    setUser(res.user)
    return res.user
  }

  function logout() {
    clearSession()
    setUser(null)
  }

  return <AuthCtx.Provider value={{ user, login, logout }}>{children}</AuthCtx.Provider>
}

export function useAuth() {
  return useContext(AuthCtx)
}
