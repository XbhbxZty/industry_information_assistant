import {
  archiveCompanyProfile,
  getCompanyProfile,
  getCompanyProfileHistory,
  searchCompanyProfileMaterials,
  type CompanyProfile,
  type CompanyProfileHistoryItem,
  type SupplementalMaterial,
} from '@/api/company-profiles'
import { authState } from '@/store/auth'
import { EditOutlined, InboxOutlined, SearchOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Descriptions, Empty, Input, Modal, Result, Space, Spin, Table, Tag, Timeline, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useSnapshot } from 'valtio'
import styles from './index.module.scss'

const { Title, Text } = Typography

function requestError(error: unknown) {
  return error instanceof Error ? error.message : '请求失败，请稍后重试'
}

function recordColumns<T extends object>(records: T[]): ColumnsType<T> {
  const keys = Array.from(new Set(records.flatMap(record => Object.keys(record)))).slice(0, 8)
  return keys.map(key => ({
    title: key,
    dataIndex: key,
    render: (value: unknown) => value === null || value === undefined || value === '' ? '—' : String(value),
  })) as ColumnsType<T>
}

function RecordSection({ title, records }: { title: string; records?: Record<string, unknown>[] }) {
  if (!records?.length) return null
  return <Card size="small" title={title}><Table rowKey={(_, index) => String(index)} size="small" dataSource={records} columns={recordColumns(records)} pagination={false} scroll={{ x: true }} /></Card>
}

export default function CompanyProfileDetailPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const { user } = useSnapshot(authState)
  const [data, setData] = useState<CompanyProfile | null>(null)
  const [history, setHistory] = useState<CompanyProfileHistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [archiving, setArchiving] = useState(false)
  const [archiveModalOpen, setArchiveModalOpen] = useState(false)
  const [archiveReason, setArchiveReason] = useState('')
  const [materialQuery, setMaterialQuery] = useState('')
  const [materialResults, setMaterialResults] = useState<SupplementalMaterial[]>([])
  const [searchingMaterials, setSearchingMaterials] = useState(false)

  const load = useCallback(async () => {
    if (!id) return
    setLoading(true)
    setError('')
    try {
      const response = await getCompanyProfile(id)
      setData(response.data)
      if (user?.is_superuser) {
        const historyResponse = await getCompanyProfileHistory(id)
        setHistory(historyResponse.data.items || [])
      }
    } catch (requestFailure: unknown) {
      setError(requestError(requestFailure))
    } finally {
      setLoading(false)
    }
  }, [id, user?.is_superuser])

  useEffect(() => {
    void load()
  }, [load])

  const registration = data?.profile.registration || {}
  const scenarioData = data?.scenario_data || {}

  const archive = async () => {
    if (!id || !data || !archiveReason.trim()) return
    setArchiving(true)
    try {
      await archiveCompanyProfile(id, {
        expected_revision: data.revision,
        change_reason: archiveReason.trim(),
      })
      message.success('企业档案已归档')
      setArchiveModalOpen(false)
      navigate('/company-profiles')
    } catch (requestFailure: unknown) {
      message.error(requestError(requestFailure))
    } finally {
      setArchiving(false)
    }
  }

  const searchMaterials = async () => {
    if (!id || !materialQuery.trim()) return
    setSearchingMaterials(true)
    try {
      const response = await searchCompanyProfileMaterials(id, materialQuery.trim())
      setMaterialResults(response.data.items || [])
    } catch (requestFailure: unknown) {
      message.error(requestError(requestFailure))
    } finally {
      setSearchingMaterials(false)
    }
  }

  if (loading) return <div className={styles.page} style={{ textAlign: 'center', paddingTop: 80 }}><Spin /></div>
  if (error || !data) return <div className={styles.page}><Result status="error" title="无法打开企业档案" subTitle={error || '档案不存在或已归档'} extra={<Button onClick={() => void load()}>重试</Button>} /></div>

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <Title level={4} style={{ margin: 0 }}>{data.profile.name}</Title>
          <div className={styles.meta}><span>版本 r{data.revision}</span><span>最近更新：{data.updated_at || '—'}</span><span>{data.profile.credit_code || '未提供统一社会信用代码'}</span></div>
        </div>
        <Space>
          {user?.is_superuser && <Button icon={<EditOutlined />} onClick={() => navigate(`/company-profiles/${id}/edit`)}>编辑</Button>}
          {user?.is_superuser && <Button danger icon={<InboxOutlined />} loading={archiving} onClick={() => setArchiveModalOpen(true)}>归档</Button>}
          <Button type="primary" onClick={() => navigate(`/due-diligence?company_profile_id=${encodeURIComponent(id)}`)}>发起尽调</Button>
        </Space>
      </div>
      <Modal
        open={archiveModalOpen}
        title="归档企业档案？"
        okText="归档"
        okButtonProps={{ danger: true, disabled: !archiveReason.trim() }}
        confirmLoading={archiving}
        onCancel={() => {
          setArchiveModalOpen(false)
          setArchiveReason('')
        }}
        onOk={() => void archive()}
      >
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Text>归档后普通用户将不可见，当前操作可在历史中追溯。</Text>
          <Input.TextArea value={archiveReason} onChange={event => setArchiveReason(event.target.value)} placeholder="必须填写归档原因" rows={3} />
        </Space>
      </Modal>
      {!user?.is_superuser && <Alert className={styles.readOnly} type="info" showIcon message="只读模式" description="您可以查看当前有效档案；创建、编辑和归档仅限管理员。" />}
      <div className={styles.detailLayout}>
        <Space direction="vertical" size={12} className={styles.detailMain}>
          <Card size="small" title="工商登记">
            <Descriptions size="small" column={2} bordered>
              {Object.entries(registration).map(([key, value]) => <Descriptions.Item key={key} label={key}>{value === null || value === undefined ? '—' : String(value)}</Descriptions.Item>)}
            </Descriptions>
          </Card>
          <RecordSection title="股东" records={data.profile.shareholders} />
          {data.profile.actual_controller && <Card size="small" title="实际控制人"><Descriptions size="small" column={1} bordered>{Object.entries(data.profile.actual_controller).map(([key, value]) => <Descriptions.Item key={key} label={key}>{String(value ?? '—')}</Descriptions.Item>)}</Descriptions></Card>}
          <RecordSection title="财务期间记录" records={data.profile.financials} />
          <RecordSection title="司法记录" records={data.profile.judicial_records} />
          <RecordSection title="对外担保" records={data.profile.guarantee} />
          <RecordSection title="关联方" records={data.profile.related_party} />
          {Object.keys(scenarioData).length > 0 && <Card size="small" title="场景数据（单独统计）"><Descriptions size="small" column={2} bordered>{Object.entries(scenarioData).map(([key, value]) => <Descriptions.Item key={key} label={key}>{String(value ?? '—')}</Descriptions.Item>)}</Descriptions></Card>}
          <Card size="small" title="字段来源">
            {data.field_sources?.length ? <Table rowKey={(_, index) => String(index)} size="small" dataSource={data.field_sources} pagination={false} columns={[
              { title: '来源 ID', dataIndex: 'source_id' },
              { title: '来源名称', dataIndex: 'name' },
              { title: '出具机构', dataIndex: 'issuer' },
              { title: '覆盖字段', dataIndex: 'field_ids', render: (fields: string[]) => fields?.join('、') || '—' },
              { title: '来源类型', dataIndex: 'source_type', render: (source: string) => <Tag>{source}</Tag> },
              { title: '获取时间', dataIndex: 'retrieved_at' },
              { title: '数据截止日', dataIndex: 'as_of_date' },
              { title: '引用', dataIndex: 'reference', render: (value?: string) => value || '—' },
              { title: 'SHA-256', dataIndex: 'sha256', render: (value?: string) => value || '—' },
            ]} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未声明字段来源" />}
          </Card>
          <Card size="small" title="补充材料">
            <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
              <Input value={materialQuery} onChange={event => setMaterialQuery(event.target.value)} onPressEnter={() => void searchMaterials()} placeholder="检索本档案的补充文本" />
              <Button icon={<SearchOutlined />} loading={searchingMaterials} onClick={() => void searchMaterials()}>检索</Button>
            </Space.Compact>
            {materialResults.length > 0 ? <Table rowKey={(_, index) => String(index)} size="small" dataSource={materialResults} columns={recordColumns(materialResults)} pagination={false} /> : data.materials?.length ? <Table rowKey={(_, index) => String(index)} size="small" dataSource={data.materials} columns={recordColumns(data.materials)} pagination={false} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无补充材料" />}
          </Card>
        </Space>
        {user?.is_superuser && <Card size="small" title="修改历史" className={styles.detailSide}>{history.length ? <Timeline items={history.map(item => ({ children: <><Text>r{item.revision} · {item.created_at} · {item.action}</Text><br /><Text type="secondary">{item.actor_id || '管理员'}：{item.change_reason}</Text></> }))} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无历史记录" />}</Card>}
      </div>
    </div>
  )
}
