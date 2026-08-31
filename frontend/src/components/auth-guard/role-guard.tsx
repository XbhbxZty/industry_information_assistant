import { authState } from '@/store/auth'
import { Button, Result } from 'antd'
import { useNavigate } from 'react-router-dom'
import { useSnapshot } from 'valtio'

interface RoleGuardProps {
  children: React.ReactNode
  subTitle?: string
  returnTo?: string
  returnLabel?: string
}

/**
 * 管理员路由守卫。
 *
 * 不把非管理员静默重定向：直接访问受限地址时应清楚说明没有权限。
 * 服务端仍是授权的最终权威。
 */
export function RoleGuard({
  children,
  subTitle = '企业档案可供登录用户查看；创建、编辑和归档仅限管理员。',
  returnTo = '/company-profiles',
  returnLabel = '返回企业档案',
}: RoleGuardProps) {
  const { user } = useSnapshot(authState)
  const navigate = useNavigate()

  if (!user?.is_superuser) {
    return (
      <Result
        status="403"
        title="无管理权限"
        subTitle={subTitle}
        extra={<Button type="primary" onClick={() => navigate(returnTo)}>{returnLabel}</Button>}
      />
    )
  }

  return <>{children}</>
}
