// Copyright © 2026 XbhbxZty
// 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
import { useState } from 'react'
import {
  Alert, Button, Card, Descriptions, Form, Input, Progress, Radio,
  Select, Space, Table, Tag, Tooltip, Typography,
} from 'antd'
import type {
  CheckStatus, Completeness, FieldCheck, HumanReviewRequest,
  ReviewDecision, RiskAssessment,
} from '@/api/duediligence'
import { authState } from '@/store/auth'
import { useSnapshot } from 'valtio'

const { Text, Paragraph } = Typography

/** 四态呈现。**颜色与文案必须让人一眼分清「查了没有」与「没查」**——
 *  这两者在授信判断上的含义完全相反，是整个系统的核心语义。 */
const STATUS_META: Record<CheckStatus, { color: string; label: string; hint: string }> = {
  verified: { color: 'green', label: '已核实', hint: '有明确结论，可作为授信依据' },
  unverified: {
    color: 'orange', label: '未核实',
    hint: '信息缺口，必须补查。**不等于「不存在」**',
  },
  conflicting: {
    color: 'red', label: '数据冲突',
    hint: '多源取值不一致，须人工核实后方可采信',
  },
  not_applicable: { color: 'default', label: '不适用', hint: '该项对此类主体无意义' },
}

const LEVEL_COLOR: Record<string, string> = {
  低风险: 'green', 中风险: 'gold', 高风险: 'orange', 拒绝: 'red',
}

// ------------------------------------------------------------------ 核实率

export function CompletenessBar({ data }: { data: Completeness | null }) {
  if (!data) return null
  const pct = Math.round((data.verified_rate || 0) * 100)
  const scenario = data.scenario
  return (
    <Card size="small" title="核心主体必查项核实率">
      <Progress
        percent={pct}
        status={pct >= 90 ? 'success' : pct >= 60 ? 'normal' : 'exception'}
        format={() => `${data.required_verified}/${data.required_total}`}
      />
      <Space size={4} wrap style={{ marginTop: 8 }}>
        {data.unverified_fields?.length > 0 && (
          <Tag color="orange">未核实 {data.unverified_fields.length} 项</Tag>
        )}
        {data.conflicting_fields?.length > 0 && (
          <Tag color="red">冲突 {data.conflicting_fields.length} 项</Tag>
        )}
        {data.capability_gaps && data.capability_gaps.length > 0 && (
          <Tooltip title="系统尚不具备核查该项的能力，重试也无法解决，须线下人工核查">
            <Tag color="purple">能力缺失 {data.capability_gaps.length} 项</Tag>
          </Tooltip>
        )}
      </Space>
      {scenario && scenario.total > 0 && (
        <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid #f0f0f0' }}>
          <Text strong style={{ fontSize: 12 }}>场景清单覆盖率（单独统计）</Text>
          <Progress
            size="small"
            percent={Math.round((scenario.rate || 0) * 100)}
            format={() => `${scenario.verified}/${scenario.total}`}
            style={{ marginTop: 4 }}
          />
          {scenario.unverified_fields?.length > 0 && <Tag color="purple">场景待核实 {scenario.unverified_fields.length} 项</Tag>}
        </div>
      )}
    </Card>
  )
}

// ------------------------------------------------------------------ 清单

export function ChecklistTable({ checks }: { checks: FieldCheck[] }) {
  if (!checks?.length) return null
  const coreChecks = checks.filter(check => !check.scope || check.scope === 'core')
  const scenarioChecks = checks.filter(check => check.scope?.startsWith('scenario:'))
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <ChecklistTableSection title="核心主体清单" checks={coreChecks} />
      {scenarioChecks.length > 0 && <ChecklistTableSection title="业务场景清单（单独统计，不影响核心核实率）" checks={scenarioChecks} scenario />}
    </Space>
  )
}

function ChecklistTableSection({ title, checks, scenario = false }: { title: string; checks: FieldCheck[]; scenario?: boolean }) {
  return (
    <Card size="small" title={title}>
      <Table<FieldCheck>
        size="small"
        rowKey="field_id"
        dataSource={checks}
        pagination={false}
        scroll={{ y: 420 }}
        columns={[
          {
            title: '核查项', dataIndex: 'field_name', width: 150,
            render: (v, r) => (
              <Space size={4}>
                <Text>{v}</Text>
                {scenario ? <Tag color="purple">场景项</Tag> : r.required && <Tag color="blue">尽调必查</Tag>}
              </Space>
            ),
          },
          {
            title: '状态', dataIndex: 'status', width: 110,
            filters: Object.entries(STATUS_META).map(([k, m]) => ({ text: m.label, value: k })),
            onFilter: (val, r) => r.status === val,
            render: (s: CheckStatus) => (
              <Tooltip title={STATUS_META[s]?.hint}>
                <Tag color={STATUS_META[s]?.color}>{STATUS_META[s]?.label ?? s}</Tag>
              </Tooltip>
            ),
          },
          {
            title: '结论 / 未核实原因',
            render: (_, r) =>
              r.status === 'conflicting' ? (
                <Space direction="vertical" size={0}>
                  {(r.conflict_detail || []).map((d, i) => (
                    <Text key={i} type="danger">{d.source}：{d.value}</Text>
                  ))}
                </Space>
              ) : r.status === 'verified' ? (
                <Text>{r.value}</Text>
              ) : (
                <Text type="warning">{r.failure_reason || '数据源未覆盖'}</Text>
              ),
          },
          {
            title: '来源 / 取证时间', width: 190,
            render: (_, r) => {
              if (r.status !== 'verified' && r.status !== 'conflicting') return <Text type="secondary">—</Text>
              const src = r.source_adapter || r.verification_origin
              return (
                <Space direction="vertical" size={0}>
                  <Text style={{ fontSize: 12 }}>{SOURCE_LABEL[src || ''] || src || '来源未标注'}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {r.retrieved_at || '取证时间未声明'}
                  </Text>
                </Space>
              )
            },
          },
        ]}
      />
    </Card>
  )
}

/** 与后端 evidence_appendix.SOURCE_LABEL 保持一致的业务可读名称 */
const SOURCE_LABEL: Record<string, string> = {
  initial_profile: '初始企业档案',
  business_registry: '工商登记信息',
  relation_registry: '关联关系登记库',
  graph_analysis: '关联关系图谱推导',
  judicial: '司法公开信息',
  financial_report: '财务报表',
  bidding: '招投标公开信息',
  public_opinion: '公开舆情',
}

// ------------------------------------------------------------------ 评级

export function RiskCard({ risk }: { risk: RiskAssessment | null }) {
  if (!risk?.level) return null
  const hr = risk.human_review
  const rec = risk.credit_recommendation
  return (
    <Card
      size="small"
      title={
        <Space>
          <span>风险评级</span>
          <Tag color={LEVEL_COLOR[risk.level] || 'default'} style={{ fontSize: 14 }}>
            {risk.level}
          </Tag>
          {hr?.completed && hr.override_level && (
            <Tooltip title={`规则引擎原始结论：${hr.engine_level}。人工改判必须留痕`}>
              <Tag color="blue">人工改判</Tag>
            </Tooltip>
          )}
        </Space>
      }
    >
      <Descriptions size="small" column={2} bordered>
        <Descriptions.Item label="综合评分">
          <Tooltip title="分数会被表现好的维度稀释，不可单独使用，以等级与闸门为准">
            {risk.composite_score} / 100 ⚠️
          </Tooltip>
        </Descriptions.Item>
        <Descriptions.Item label="人工复核">
          {risk.requires_human_review ? <Text type="danger">必须</Text> : '非强制'}
        </Descriptions.Item>
        <Descriptions.Item label="授信建议" span={2}>{risk.credit_advice}</Descriptions.Item>
      </Descriptions>

      {hr?.completed && (
        <Alert
          style={{ marginTop: 12 }}
          type={hr.approved ? 'success' : 'error'}
          message={`人工复核：${hr.approved ? '通过' : '未通过'}（复核人 ${hr.reviewer}）`}
          description={
            <>
              {hr.override_level && (
                <div>等级由规则引擎的 <b>{hr.engine_level}</b> 调整为 <b>{hr.override_level}</b></div>
              )}
              <div>{hr.comment || '（未填写意见）'}</div>
            </>
          }
        />
      )}

      {risk.gates_applied?.length > 0 && (
        <>
          <Paragraph strong style={{ marginTop: 12, marginBottom: 4 }}>
            触发的闸门（等级往往由闸门而非分数决定）
          </Paragraph>
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            {risk.gates_applied.map((g, i) => <li key={i}><Text>{g}</Text></li>)}
          </ul>
        </>
      )}

      {rec && <CreditBlock rec={rec} />}

      {risk.triggered_rules?.length > 0 && (
        <>
          <Paragraph strong style={{ marginTop: 12, marginBottom: 4 }}>触发的评分规则</Paragraph>
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            {risk.triggered_rules
              .filter(r => r.score > 0)
              .sort((a, b) => b.score - a.score)
              .map((r, i) => (
                <li key={i}>
                  <Text>[{r.dimension}] {r.detail}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>（{r.field_id}，{r.score} 分）</Text>
                </li>
              ))}
          </ul>
        </>
      )}
    </Card>
  )
}

function CreditBlock({ rec }: { rec: NonNullable<RiskAssessment['credit_recommendation']> }) {
  return (
    <div style={{ marginTop: 12 }}>
      <Paragraph strong style={{ marginBottom: 4 }}>授信额度建议（规则测算，非模型判断）</Paragraph>
      {rec.recommendable ? (
        <Alert
          type="info"
          message={`建议区间 ${rec.range_low}–${rec.range_high} 万元（测算中值 ${rec.suggested_amount} 万元）`}
        />
      ) : (
        // 不出具额度时同样要给依据——「不出具」本身也是一个需要理由的结论
        <Alert type="warning" message="不出具额度建议" description={rec.reason} />
      )}
      {(rec.basis?.length > 0 || (rec.unavailable_bases?.length ?? 0) > 0) && (
        <Table
          style={{ marginTop: 8 }}
          size="small" pagination={false} rowKey={(_, i) => String(i)}
          dataSource={[
            ...rec.basis.map(b => ({ k: b.method, v: `${b.value.toFixed(0)} 万元`, d: b.detail })),
            // 金额列写「不参与测算」而不是 0：0 是现金流法为负时的真实取值，
            // 两者混在同一列里必然被误读（BC-72）
            ...(rec.unavailable_bases ?? []).map(u => ({
              k: u.method, v: '不参与测算', d: u.reason })),
            ...rec.adjustments.map(a => ({ k: a.factor, v: `×${a.multiplier}`, d: a.detail })),
            ...rec.deductions.map(d => ({ k: d.item, v: `−${d.amount.toFixed(0)} 万元`, d: d.detail })),
          ]}
          columns={[
            { title: '测算口径', dataIndex: 'k', width: 150 },
            { title: '金额', dataIndex: 'v', width: 110 },
            { title: '依据', dataIndex: 'd' },
          ]}
        />
      )}
      {rec.conditions?.length > 0 && (
        <>
          <Paragraph strong style={{ marginTop: 8, marginBottom: 4 }}>放款条件与增信要求</Paragraph>
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            {rec.conditions.map((c, i) => <li key={i}><Text>{c}</Text></li>)}
          </ul>
        </>
      )}
    </div>
  )
}

// ------------------------------------------------------------ 人工复核卡片

export function ReviewCard({
  req, onSubmit, submitting, canSubmit = false,
}: {
  req: HumanReviewRequest
  onSubmit: (d: ReviewDecision) => void
  submitting: boolean
  /** Only a dedicated reviewer surface may enable submission. */
  canSubmit?: boolean
}) {
  const [form] = Form.useForm()
  const [approved, setApproved] = useState(true)
  const { user } = useSnapshot(authState)

  return (
    <Card
      title={<Space><Text strong type="danger">⏸ 待风控复核</Text>
        <Tag color={LEVEL_COLOR[req.level] || 'default'}>{req.level}</Tag></Space>}
      style={{ borderColor: '#1677ff' }}
    >
      {/* 暂停 ≠ 完成。必须让复核人明白此刻的结论尚未生效 */}
      <Alert
        type="warning" showIcon style={{ marginBottom: 12 }}
        message="本次尽调已暂停，结论尚未生效"
        description="系统判定该笔业务需人工确认。未提交复核结论前，本报告不得作为授信依据。"
      />

      <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }}>
        <Descriptions.Item label="尽调对象">{req.company_name || '—'}</Descriptions.Item>
        <Descriptions.Item label="必查项核实率">
          {typeof req.verified_rate === 'number' ? `${Math.round(req.verified_rate * 100)}%` : '—'}
        </Descriptions.Item>
        <Descriptions.Item label="规则引擎结论" span={2}>
          {req.level}｜{req.credit_advice}
        </Descriptions.Item>
      </Descriptions>

      {req.gates_applied?.length > 0 && (
        <Alert
          type="info" style={{ marginBottom: 12 }}
          message="等级由以下闸门决定（不是由分数决定）"
          description={<ul style={{ margin: 0, paddingLeft: 18 }}>
            {req.gates_applied.map((g, i) => <li key={i}>{g}</li>)}
          </ul>}
        />
      )}

      {(req.unverified_fields?.length > 0 || req.conflicting_fields?.length > 0) && (
        <Space wrap style={{ marginBottom: 12 }}>
          {req.unverified_fields?.map(f => <Tag key={f} color="orange">未核实：{f}</Tag>)}
          {req.conflicting_fields?.map(f => <Tag key={f} color="red">冲突：{f}</Tag>)}
        </Space>
      )}

      {!canSubmit ? (
        <Alert
          type="info"
          showIcon
          message="等待独立复核人员处理"
          description={(
            <Space direction="vertical" size={2}>
              <Text>发起人不能审核自己的尽调。请将此任务交由具备复核权限且非本次发起人的人员处理。</Text>
              <Text type="secondary" copyable={{ text: req.session_id }}>
                会话标识：{req.session_id}
              </Text>
            </Space>
          )}
        />
      ) : (
        <Form
          form={form} layout="vertical"
          onFinish={(v) => onSubmit({
            approved: v.approved,
            comment: v.comment || '',
            override_level: v.override_level || null,
          })}
          initialValues={{ approved: true }}
        >
          <Form.Item label="复核人">
            <Text>{user?.username || '当前登录用户'}</Text>
            <Text type="secondary">（由登录身份自动签名，不可手工修改）</Text>
          </Form.Item>

          <Form.Item name="approved" label="复核结论">
            <Radio.Group onChange={e => setApproved(e.target.value)}>
              <Radio.Button value={true}>通过</Radio.Button>
              <Radio.Button value={false}>不通过</Radio.Button>
            </Radio.Group>
          </Form.Item>

          {approved && (
            <Form.Item
              name="override_level" label="调整风险等级（可选）"
              extra="规则引擎的原始结论会被完整保留并写入报告——改写必须留痕"
            >
              <Select allowClear placeholder="沿用规则引擎结论" style={{ maxWidth: 260 }}
                options={['低风险', '中风险', '高风险', '拒绝'].map(v => ({ value: v, label: v }))} />
            </Form.Item>
          )}

          <Form.Item
            name="comment"
            label="复核意见"
            dependencies={['approved', 'override_level']}
            rules={[({ getFieldValue }) => ({
              validator(_, value) {
                const changed = getFieldValue('override_level')
                const rejected = getFieldValue('approved') === false
                if ((changed || rejected) && !String(value || '').trim()) {
                  return Promise.reject(new Error('改判或不通过时必须填写理由'))
                }
                return Promise.resolve()
              },
            })]}
          >
            <Input.TextArea rows={3} placeholder="调整等级时请写明理由，该意见会进入报告正文" />
          </Form.Item>

          <Button type="primary" htmlType="submit" loading={submitting}>
            提交复核并继续
          </Button>
        </Form>
      )}
    </Card>
  )
}
