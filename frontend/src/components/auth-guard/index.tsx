// Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
// 未经授权，禁止转售或仿制。
//
// 本文件在原课程项目基础上二次开发（已获授权）。
// 改造部分 © 2026 XbhbxZty
import { authActions, authState } from '@/store/auth'
import { getCurrentUser } from '@/api/auth'
import { useEffect, useState } from 'react'
import { Alert, Button, Spin } from 'antd'
import { Navigate, useLocation } from 'react-router-dom'
import { useSnapshot } from 'valtio'

interface AuthGuardProps {
  children: React.ReactNode
}

/**
 * 认证守卫组件
 *
 * 检查用户是否已登录，未登录则重定向到登录页面
 * 登录后会自动跳转回原来的页面
 */
export function AuthGuard({ children }: AuthGuardProps) {
  const { isLoggedIn, token } = useSnapshot(authState)
  const location = useLocation()
  const [validatedToken, setValidatedToken] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    let active = true
    if (!isLoggedIn || !token) return
    setFailed(false)
    getCurrentUser().then(({ data }) => {
      if (!active || authState.token !== token) return
      authActions.updateUser(data)
      setValidatedToken(token)
    }).catch(() => {
      if (active && authState.token === token) setFailed(true)
    })
    return () => { active = false }
  }, [isLoggedIn, token, retry])

  if (!isLoggedIn) {
    // 保存当前路径，登录后可以跳转回来
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  if (validatedToken !== token) {
    return failed
      ? <Alert type="error" message="暂时无法验证登录状态，请检查服务后重试。" action={<Button onClick={() => setRetry(value => value + 1)}>重试</Button>} />
      : <Spin tip="正在验证登录状态"><div style={{ minHeight: 120 }} /></Spin>
  }

  return <>{children}</>
}
