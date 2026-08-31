import {
  getReviewDetail,
  getReviewQueue,
  submitReview,
  type HumanReviewRequest,
  type ReviewDecision,
  type ReviewDetail,
  type ReviewQueueItem,
} from '@/api/duediligence'
import Markdown from '@/components/markdown'
import { ReviewCard } from '@/pages/due-diligence/components'
import { Alert, Button, Card, Col, Descriptions, Empty, List, Row, Space, Spin, Tag, Typography } from 'antd'
import { isAxiosError } from 'axios'
import { useCallback, useEffect, useState } from 'react'

const { Title, Text } = Typography

type StreamEvent = {
  type?: string
  content?: unknown
  message?: string
}

function requestErrorMessage(error: unknown, action: 'load' | 'detail' | 'submit') {
  if (isAxiosError(error)) {
    const status = error.response?.status
    const detail = error.response?.data?.detail || error.response?.data?.message || error.message
    if (status === 403) {
      const text = String(detail || '')
      if (text.includes('暂停') || text.includes('待人工复核') || text.includes('有效的待')) {
        return `任务状态已变化，已不再处于可复核的暂停状态：${text}`
      }
      return `无权访问该复核任务：仅独立复核人员可以处理非本人发起的待办。${text ? ` ${text}` : ''}`
    }
    if (status === 404) return action === 'detail' ? '该复核任务不存在，或已不再可供复核。' : '复核任务不存在。'
    if (status === 400) return action === 'submit'
      ? `任务状态已变化，无法提交复核：${detail || '当前不在待复核暂停状态。'}`
      : String(detail || '请求状态不正确。')
    if (status === 409) return '该待复核任务的完整性校验未通过，系统已拒绝展示或提交该任务。'
    return String(detail || '请求失败，请稍后重试。')
  }
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}

/**
 * 审核流会在 HTTP 200 后以 SSE 报告业务错误，所以不能只依赖 axios 的状态码。
 * 这里逐帧消费，遇到后端 error 事件即失败关闭并把消息留给复核人。
 */
async function consumeReviewStream(body: ReadableStream<Uint8Array>) {
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let reachedTerminalState = false

  const processPart = (part: string) => {
    for (const line of part.split('\n')) {
      if (!line.startsWith('data: ')) continue
      const payload = line.slice(6).trim()
      if (!payload || payload === '[DONE]') continue
      try {
        const event = JSON.parse(payload) as StreamEvent
        if (event.type === 'error') {
          const content = typeof event.content === 'string' ? event.content : event.message
          throw new Error(content || '复核流返回了未说明的错误')
        }
        if (event.type === 'research_complete') reachedTerminalState = true
      } catch (error) {
        // JSON 不完整的单帧不能伪装成已完成；后端明确 error 同样必须上抛。
        if (error instanceof SyntaxError) {
          throw new Error('复核流包含无法解析的事件，未确认复核结论是否生效。请刷新待办后核对。')
        }
        throw error
      }
    }
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')
    buffer = parts.pop() || ''
    for (const part of parts) processPart(part)
  }
  buffer += decoder.decode()
  if (buffer.trim()) processPart(buffer)
  if (!reachedTerminalState) {
    throw new Error('复核流在终局确认前结束，未确认复核结论是否生效。请刷新待办后核对。')
  }
}

function reviewRequest(detail: ReviewDetail): HumanReviewRequest {
  const risk = detail.risk_assessment
  const completeness = detail.completeness
  return {
    type: 'human_review_required',
    session_id: detail.session_id,
    company_name: detail.company_name,
    level: risk.level || '待复核',
    composite_score: risk.composite_score || 0,
    credit_advice: risk.credit_advice || '请结合下方报告与风险依据作出复核结论。',
    gates_applied: risk.gates_applied,
    verified_rate: completeness.verified_rate || 0,
    unverified_fields: completeness.unverified_fields,
    conflicting_fields: completeness.conflicting_fields,
    critical_issues: detail.critical_issues || [],
    errors: detail.errors || [],
    profile_ref: detail.profile_ref,
  }
}

function evidenceText(item: ReviewDetail['evidence'][number]) {
  return [item.evidence_id, item.field_id, item.source_adapter, item.as_of_date]
    .filter((value): value is string => typeof value === 'string' && Boolean(value))
    .join(' · ')
}

export default function RiskReviewPage() {
  const [items, setItems] = useState<ReviewQueueItem[]>([])
  const [detail, setDetail] = useState<ReviewDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const loadQueue = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await getReviewQueue()
      setItems(response.data.items || [])
    } catch (requestError: unknown) {
      setItems([])
      setError(requestErrorMessage(requestError, 'load'))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadDetail = useCallback(async (sessionId: string) => {
    setDetailLoading(true)
    setError('')
    setSuccess('')
    try {
      const response = await getReviewDetail(sessionId)
      setDetail(response.data)
    } catch (requestError: unknown) {
      setDetail(null)
      setError(requestErrorMessage(requestError, 'detail'))
    } finally {
      setDetailLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadQueue()
  }, [loadQueue])

  const refresh = async () => {
    setDetail(null)
    setSuccess('')
    await loadQueue()
  }

  const submit = async (decision: ReviewDecision) => {
    if (!detail) return
    setSubmitting(true)
    setError('')
    setSuccess('')
    try {
      const response = await submitReview(detail.session_id, decision)
      await consumeReviewStream(response.data)
      setDetail(null)
      setSuccess('复核结论已提交并完成后续流程；待办列表已刷新。')
      await loadQueue()
    } catch (requestError: unknown) {
      setError(requestErrorMessage(requestError, 'submit'))
    } finally {
      setSubmitting(false)
    }
  }

  const risk = detail?.risk_assessment
  const completeness = detail?.completeness

  return (
    <div style={{ padding: '8px 0' }}>
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center' }}>
          <div>
            <Title level={4} style={{ margin: 0 }}>风控复核</Title>
            <Text type="secondary">仅显示非本人发起、处于待复核状态的最小材料包。</Text>
          </div>
          <Button onClick={() => void refresh()} loading={loading}>刷新待办</Button>
        </div>

        {error && <Alert type="error" showIcon message="复核操作未完成" description={error} closable onClose={() => setError('')} />}
        {success && <Alert type="success" showIcon message={success} closable onClose={() => setSuccess('')} />}

        <Row gutter={12}>
          <Col xs={24} lg={8}>
            <Card size="small" title="待复核任务">
              {loading ? <div style={{ textAlign: 'center', padding: 32 }}><Spin /></div> : items.length === 0 ? (
                <Empty description="当前没有可处理的复核任务" />
              ) : (
                <List
                  dataSource={items}
                  renderItem={(item) => (
                    <List.Item
                      actions={[<Button key="open" type="link" onClick={() => void loadDetail(item.session_id)}>查看材料</Button>]}
                    >
                      <List.Item.Meta
                        title={<Space size={4}><Text strong>{item.company_name}</Text>{item.risk_assessment.level && <Tag>{item.risk_assessment.level}</Tag>}</Space>}
                        description={<Space direction="vertical" size={0}>
                          <Text type="secondary" style={{ fontSize: 12 }}>会话：{item.session_id}</Text>
                          {item.updated_at && <Text type="secondary" style={{ fontSize: 12 }}>更新：{item.updated_at}</Text>}
                        </Space>}
                      />
                    </List.Item>
                  )}
                />
              )}
            </Card>
          </Col>

          <Col xs={24} lg={16}>
            {detailLoading ? <Card><div style={{ textAlign: 'center', padding: 56 }}><Spin tip="正在验证并加载最小复核材料包…" /></div></Card> : !detail ? (
              <Card><Empty description="从左侧选择一项待复核任务" /></Card>
            ) : (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <Card size="small" title="任务信息">
                  <Descriptions size="small" column={2} bordered>
                    <Descriptions.Item label="企业名称">{detail.company_name}</Descriptions.Item>
                    <Descriptions.Item label="会话标识">{detail.session_id}</Descriptions.Item>
                    <Descriptions.Item label="创建时间">{detail.created_at || '—'}</Descriptions.Item>
                    <Descriptions.Item label="更新时间">{detail.updated_at || '—'}</Descriptions.Item>
                    <Descriptions.Item label="冻结档案" span={2}>
                      {detail.profile_ref ? `管理员档案 r${detail.profile_ref.revision}` : '未使用管理员档案'}
                    </Descriptions.Item>
                  </Descriptions>
                </Card>

                <ReviewCard key={detail.session_id} req={reviewRequest(detail)} onSubmit={submit} submitting={submitting} canSubmit />
                <Card size="small" title="规则风险依据">
                  <Descriptions size="small" column={2} bordered>
                    <Descriptions.Item label="规则风险等级">{risk?.level || '—'}</Descriptions.Item>
                    <Descriptions.Item label="综合评分">{risk?.composite_score ?? '—'}</Descriptions.Item>
                    <Descriptions.Item label="必查项核实率">
                      {typeof completeness?.verified_rate === 'number' ? `${Math.round(completeness.verified_rate * 100)}%` : '—'}
                    </Descriptions.Item>
                    <Descriptions.Item label="已核实/必查">
                      {completeness?.required_verified ?? '—'} / {completeness?.required_total ?? '—'}
                    </Descriptions.Item>
                    <Descriptions.Item label="规则授信意见" span={2}>{risk?.credit_advice || '—'}</Descriptions.Item>
                  </Descriptions>
                  {risk?.gates_applied.length ? <Alert type="info" showIcon style={{ marginTop: 12 }} message="触发闸门" description={<ul style={{ margin: 0, paddingLeft: 18 }}>{risk.gates_applied.map((gate) => <li key={gate}>{gate}</li>)}</ul>} /> : null}
                  {risk?.triggered_rules.length ? <Alert type="warning" showIcon style={{ marginTop: 12 }} message="触发规则" description={<ul style={{ margin: 0, paddingLeft: 18 }}>{risk.triggered_rules.map((rule) => <li key={rule}>{rule}</li>)}</ul>} /> : null}
                  {(completeness?.unverified_fields.length || completeness?.conflicting_fields.length) ? <Space wrap style={{ marginTop: 12 }}>
                    {completeness?.unverified_fields.map((field) => <Tag color="orange" key={`unverified-${field}`}>未核实：{field}</Tag>)}
                    {completeness?.conflicting_fields.map((field) => <Tag color="red" key={`conflict-${field}`}>冲突：{field}</Tag>)}
                  </Space> : null}
                  {risk?.credit_recommendation && <Descriptions size="small" column={2} bordered style={{ marginTop: 12 }}>
                    <Descriptions.Item label="可建议授信">{risk.credit_recommendation.recommendable === true ? '是' : risk.credit_recommendation.recommendable === false ? '否' : '—'}</Descriptions.Item>
                    <Descriptions.Item label="建议额度">{risk.credit_recommendation.suggested_amount ?? '—'} {risk.credit_recommendation.currency || ''}</Descriptions.Item>
                    <Descriptions.Item label="额度意见" span={2}>{risk.credit_recommendation.advice_text || '—'}</Descriptions.Item>
                  </Descriptions>}
                  {risk?.credit_recommendation?.conditions.length ? <Alert type="info" showIcon style={{ marginTop: 12 }} message="授信条件" description={<ul style={{ margin: 0, paddingLeft: 18 }}>{risk.credit_recommendation.conditions.map((condition) => <li key={condition}>{condition}</li>)}</ul>} /> : null}
                </Card>

                {detail.critical_issues.length > 0 && (
                  <Alert
                    type="error"
                    showIcon
                    message="关键问题"
                    description={<ul style={{ margin: 0, paddingLeft: 18 }}>{detail.critical_issues.map((issue, index) => <li key={index}>{issue.description || issue.issue_type || '未说明的关键问题'}</li>)}</ul>}
                  />
                )}

                {detail.errors && detail.errors.length > 0 && (
                  <Alert
                    type="warning"
                    showIcon
                    message="执行错误与证据降级摘要"
                    description={<ul style={{ margin: 0, paddingLeft: 18 }}>{detail.errors.map((item, index) => <li key={index}>{item}</li>)}</ul>}
                  />
                )}
                {detail.evidence.length > 0 && (
                  <Card size="small" title="可公开证据摘要">
                    <ul style={{ margin: 0, paddingLeft: 18 }}>{detail.evidence.map((item) => <li key={item.evidence_id}>{evidenceText(item)}</li>)}</ul>
                  </Card>
                )}
                {detail.final_report && (
                  <Card size="small" title="尽职调查报告草稿">
                    <Markdown value={detail.final_report} gfm safe />
                  </Card>
                )}
              </Space>
            )}
          </Col>
        </Row>
      </Space>
    </div>
  )
}
