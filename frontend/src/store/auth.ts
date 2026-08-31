// Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
// 未经授权，禁止转售或仿制。
//
// 本文件在原课程项目基础上二次开发（已获授权）。
// 改造部分 © 2026 XbhbxZty
import { proxy, subscribe } from 'valtio'

export interface UserInfo {
  id: string
  username: string
  email: string
  is_active: boolean
  is_superuser: boolean
  /** 服务端下发的独立复核能力；旧会话缺失时必须按无权限处理。 */
  can_human_review: boolean
  created_at: string
}

interface AuthState {
  token: string | null
  user: UserInfo | null
  isLoggedIn: boolean
}

const AUTH_STORAGE_KEY = 'auth'

// 从 localStorage 加载初始状态
function loadAuthState(): AuthState {
  try {
    const saved = localStorage.getItem(AUTH_STORAGE_KEY)
    if (saved) {
      const parsed = JSON.parse(saved) as Partial<AuthState>
      // 兼容第三阶段前存下来的会话：旧数据没有管理员字段时必须按普通用户处理。
      const user = parsed.user
        ? {
            ...parsed.user,
            is_superuser: Boolean(parsed.user.is_superuser),
            can_human_review: Boolean(parsed.user.can_human_review),
          }
        : null
      return {
        token: parsed.token || null,
        user,
        isLoggedIn: Boolean(parsed.isLoggedIn && parsed.token && user),
      }
    }
  } catch (e) {
    console.error('Failed to load auth state:', e)
  }
  return {
    token: null,
    user: null,
    isLoggedIn: false,
  }
}

// 保存状态到 localStorage
function saveAuthState(state: AuthState) {
  try {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(state))
  } catch (e) {
    console.error('Failed to save auth state:', e)
  }
}

export const authState = proxy<AuthState>(loadAuthState())

// 订阅状态变化，自动保存
subscribe(authState, () => {
  saveAuthState({
    token: authState.token,
    user: authState.user,
    isLoggedIn: authState.isLoggedIn,
  })
})

export const authActions = {
  login(token: string, user: UserInfo) {
    authState.token = token
    authState.user = user
    authState.isLoggedIn = true
  },

  logout() {
    authState.token = null
    authState.user = null
    authState.isLoggedIn = false
    localStorage.removeItem(AUTH_STORAGE_KEY)
  },

  updateUser(user: Partial<UserInfo>) {
    if (authState.user) {
      Object.assign(authState.user, user)
    }
  },
}
