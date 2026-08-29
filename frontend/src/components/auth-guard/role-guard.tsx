import { authState } from '@/store/auth'
import { Button, Result } from 'antd'
import { useNavigate } from 'react-router-dom'
import { useSnapshot } from 'valtio'

interface RoleGuardProps {
  children: React.ReactNode
}

/**
 * 管理员路由守卫。
 *
 * 不把非管理员重定向到列表：直接访问编辑地址时应清楚说明没有权限，避免误以为
 * 页面或档案不存在。服务端仍是写接口的最终授权方。
 */
export function RoleGuard({ children }: RoleGuardProps) {
  const { user } = useSnapshot(authState)
  const navigate = useNavigate()

  if (!user?.is_superuser) {
    return (
      <Result
        status="403"
        title="无管理权限"
        subTitle="企业档案可供登录用户查看；创建、编辑和归档仅限管理员。"
        extra={<Button type="primary" onClick={() => navigate('/company-profiles')}>返回企业档案</Button>}
      />
    )
  }

  return <>{children}</>
}
