import {
  createCompanyProfile,
  getCompanyProfile,
  getCompanyProfileTemplate,
  updateCompanyProfile,
  type CompanyProfile,
  type CompanyProfileTemplate,
} from '@/api/company-profiles'
import { Alert, Button, Result, Spin, Typography, message } from 'antd'
import { Form } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ProfileForm, type CompanyProfileFormValues } from './ProfileForm'
import styles from './index.module.scss'

const { Title, Text } = Typography

function statusOf(error: unknown) {
  if (typeof error === 'object' && error !== null && 'response' in error) {
    const response = (error as { response?: { status?: number } }).response
    return response?.status
  }
  return undefined
}

function failureText(error: unknown) {
  const status = statusOf(error)
  if (status === 403) return '当前账号已没有编辑权限，未保存任何修改。'
  if (status === 409) return '档案已被其他管理员更新，请重新加载后核对并再次提交。'
  if (status === 422) return '部分字段不符合服务端规则，请核对表单中的结构化记录。'
  return error instanceof Error ? error.message : '保存失败，请稍后重试。'
}

export default function CompanyProfileEditorPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [form] = Form.useForm<CompanyProfileFormValues>()
  const [template, setTemplate] = useState<CompanyProfileTemplate | null>(null)
  const [current, setCurrent] = useState<CompanyProfile | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [errorStatus, setErrorStatus] = useState<number>()
  const editing = Boolean(id)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    setErrorStatus(undefined)
    try {
      const templateResponse = await getCompanyProfileTemplate()
      setTemplate(templateResponse.data)
      if (id) {
        const profileResponse = await getCompanyProfile(id)
        const profile = profileResponse.data
        setCurrent(profile)
        form.setFieldsValue({
          profile: profile.profile,
          scenario: profile.scenario || '',
          scenario_data: profile.scenario_data || {},
          field_sources: profile.field_sources || [],
          materials: profile.materials || [],
        } as unknown as CompanyProfileFormValues)
      }
    } catch (requestFailure: unknown) {
      setErrorStatus(statusOf(requestFailure))
      setError(failureText(requestFailure))
    } finally {
      setLoading(false)
    }
  }, [form, id])

  useEffect(() => {
    void load()
  }, [load])

  const submit = async (values: CompanyProfileFormValues) => {
    const { change_reason, ...draft } = values
    setSaving(true)
    setError('')
    setErrorStatus(undefined)
    try {
      const response = editing && id && current
        ? await updateCompanyProfile(id, { ...draft, expected_revision: current.revision, change_reason })
        : await createCompanyProfile(draft)
      message.success(editing ? '企业档案已更新' : '企业档案已创建')
      navigate(`/company-profiles/${response.data.id}`, { replace: true })
    } catch (requestFailure: unknown) {
      setErrorStatus(statusOf(requestFailure))
      setError(failureText(requestFailure))
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className={styles.page} style={{ textAlign: 'center', paddingTop: 80 }}><Spin /></div>
  if (error && !template) return <div className={styles.page}><Result status="error" title="无法加载档案编辑器" subTitle={error} extra={<Button onClick={() => void load()}>重试</Button>} /></div>

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <Title level={4} style={{ margin: 0 }}>{editing ? '编辑企业档案' : '新建企业档案'}</Title>
          <Text type="secondary">以结构化字段录入；未提供的信息会明确保留为尽调缺口。</Text>
        </div>
        <Button onClick={() => navigate(editing && id ? `/company-profiles/${id}` : '/company-profiles')}>取消</Button>
      </div>
      {error && <Alert type={errorStatus === 409 ? 'warning' : 'error'} showIcon message="保存未完成" description={error} style={{ marginBottom: 12 }} />}
      <div className={styles.formWrap}><ProfileForm form={form} template={template} editing={editing} submitting={saving} onSubmit={submit} /></div>
    </div>
  )
}
