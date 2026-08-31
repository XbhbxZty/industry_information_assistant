import { authState } from '@/store/auth'
import { Button, Result } from 'antd'
import { useNavigate } from 'react-router-dom'
import { useSnapshot } from 'valtio'

/**
 * Reviewer UI guard.  This is intentionally separate from RoleGuard: reviewers
 * are a server-defined allowlist and need not be administrators.
 */
export function ReviewerGuard({ children }: { children: React.ReactNode }) {
  const { user } = useSnapshot(authState)
  const navigate = useNavigate()

  if (!user?.can_human_review) {
    return (
      <Result
        status="403"
        title="无风控复核权限"
        subTitle="该工作台仅向服务端授予复核能力的独立风控人员开放。"
        extra={<Button type="primary" onClick={() => navigate('/due-diligence')}>返回贷前尽调</Button>}
      />
    )
  }

  return <>{children}</>
}
