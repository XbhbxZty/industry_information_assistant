import { getCompanyProfiles, type CompanyProfileSummary } from '@/api/company-profiles'
import { authState } from '@/store/auth'
import { FileAddOutlined, SearchOutlined } from '@ant-design/icons'
import { Button, Empty, Input, Result, Spin, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useSnapshot } from 'valtio'
import styles from './index.module.scss'

const { Title, Text } = Typography

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '企业档案加载失败，请稍后重试'
}

export default function CompanyProfileListPage() {
  const navigate = useNavigate()
  const { user } = useSnapshot(authState)
  const [keyword, setKeyword] = useState('')
  const [rows, setRows] = useState<CompanyProfileSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await getCompanyProfiles({ keyword: keyword.trim() || undefined, page: 1, page_size: 100 })
      setRows(response.data.items || [])
    } catch (requestError: unknown) {
      setError(errorMessage(requestError))
    } finally {
      setLoading(false)
    }
  }, [keyword])

  useEffect(() => {
    void load()
  }, [load])

  const columns: ColumnsType<CompanyProfileSummary> = [
    {
      title: '企业名称', dataIndex: 'name', render: (name: string, row) => (
        <Button type="link" className={styles.listName} onClick={() => navigate(`/company-profiles/${row.id}`)}>{name}</Button>
      ),
    },
    { title: '统一社会信用代码', dataIndex: 'credit_code', render: (value?: string) => value || '—' },
    { title: '业务场景', dataIndex: 'scenario', render: (value: string) => value === 'factoring' ? <Tag color="purple">应收账款保理</Tag> : <Tag>通用</Tag> },
    { title: '版本', dataIndex: 'revision', width: 80, render: (value: number) => `r${value}` },
    { title: '最近更新', dataIndex: 'updated_at', width: 180, render: (value?: string) => value || '—' },
  ]

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <Title level={4} style={{ margin: 0 }}>企业档案</Title>
          <Text type="secondary">结构化企业资料、场景字段与来源声明。所有登录用户可查看当前有效档案。</Text>
        </div>
        {user?.is_superuser && <Button type="primary" icon={<FileAddOutlined />} onClick={() => navigate('/company-profiles/new')}>新建档案</Button>}
      </div>
      <div className={styles.toolbar}>
        <Input
          value={keyword}
          allowClear
          prefix={<SearchOutlined />}
          placeholder="按企业名称或统一社会信用代码搜索"
          onChange={event => setKeyword(event.target.value)}
          onPressEnter={() => void load()}
          style={{ maxWidth: 420 }}
        />
        <Button onClick={() => void load()}>搜索</Button>
      </div>
      {loading ? <div style={{ textAlign: 'center', padding: 64 }}><Spin /></div> : error ? (
        <Result status="error" title="无法加载企业档案" subTitle={error} extra={<Button onClick={() => void load()}>重试</Button>} />
      ) : rows.length === 0 ? (
        <Empty description={keyword ? '没有匹配的企业档案' : '暂无企业档案'} />
      ) : (
        <Table rowKey="id" columns={columns} dataSource={rows} pagination={false} />
      )}
    </div>
  )
}
