// Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
// 未经授权，禁止转售或仿制。
//
// 本文件在原课程项目基础上二次开发（已获授权）。
// 改造部分 © 2026 XbhbxZty
/**
 * 认证插件：自动添加 Token 到请求头
 */
import { IRequestPlugin } from './plugin'
import { authActions, authState } from '@/store/auth'

export const authPlugin: IRequestPlugin = {
  preinstall(instance) {
    instance.interceptors.request.use(
      (config) => {
        const token = authState.token
        if (token) {
          config.headers.Authorization = `Bearer ${token}`
        }
        return config
      },
      (error) => Promise.reject(error)
    )
    instance.interceptors.response.use(response => response, error => {
      const config = error.response?.config ?? error.config
      const sent = config?.headers?.Authorization
      // A delayed failure from an old session must not log out a newly signed-in user.
      // Login failures and permission denials are not expired-session events.
      const isLogin = /\/auth\/(login|register)(?:\?|$)/.test(config?.url ?? '')
      if (error.response?.status === 401 && !isLogin && authState.token &&
          sent === `Bearer ${authState.token}`) {
        authActions.logout()
        window.$app?.message.warning('登录状态已失效，请重新登录（服务重启或切换数据库后可能需要重新认证）')
      }
      return Promise.reject(error)
    })
  },
}
