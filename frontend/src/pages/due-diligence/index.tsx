// Copyright © 2026 XbhbxZty
// 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Checkbox, Col, Empty, Input, Row, Select, Space, Spin, Tag, Tooltip, Typography } from 'antd'
import Markdown from '@/components/markdown'
import { getKnowledgeBases, type KnowledgeBase } from '@/api/knowledge'
import { getCompanyProfiles, type CompanyProfileSummary } from '@/api/company-profiles'
import { useLocation } from 'react-router-dom'
import { ChecklistTable, CompletenessBar, ReviewCard, RiskCard } from './components'
import { InvestigationPanel } from './investigation'
import { useDDStream } from './useDDStream'
import { describeResearchOutcome } from './outcome'
import styles from './index.module.scss'

const { Title, Text } = Typography

const SAMPLES = [
  '请对东莞市泰锐精密传动件有限公司做贷前尽职调查，授信2000万元',
  '请对郑州宏川物流供应链有限公司做贷前尽职调查，授信3000万元',
  '请对苏州恒晟自动化设备有限公司做贷前尽职调查，授信5000万元',
]

export default function DueDiligencePage() {
  const { state, start, review, reset } = useDDStream()
  const location = useLocation()
  const [query, setQuery] = useState('')
  const [subjectName, setSubjectName] = useState('')
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([])
  const [companyProfiles, setCompanyProfiles] = useState<CompanyProfileSummary[]>([])
  const [companyProfileId, setCompanyProfileId] = useState(() => new URLSearchParams(location.search).get('company_profile_id') || undefined)
  const [kbName, setKbName] = useState<string>()
  const [asOf, setAsOf] = useState('')
  const [researchStrategy, setResearchStrategy] = useState<'workflow' | 'agent'>('workflow')
  const [searchWeb, setSearchWeb] = useState(false)

  useEffect(() => {
    getKnowledgeBases()
      .then(res => setKnowledgeBases(res.data || []))
      .catch(() => setKnowledgeBases([]))
    getCompanyProfiles({ offset: 0, limit: 100 })
      .then(res => setCompanyProfiles(res.data.items || []))
      .catch(() => setCompanyProfiles([]))
  }, [])

  const selectedProfile = useMemo(
    () => companyProfiles.find(profile => profile.id === companyProfileId),
    [companyProfileId, companyProfiles],
  )

  useEffect(() => {
    if (selectedProfile) setSubjectName(selectedProfile.name)
  }, [selectedProfile])

  const canLaunch = Boolean(query.trim() && subjectName.trim() && (!companyProfileId || selectedProfile))
  const launch = () => canLaunch && start(query.trim(), {
    kbName,
    asOf: asOf.trim() || undefined,
    companyProfileId,
    subjectName: selectedProfile?.name || subjectName.trim(),
    businessType: selectedProfile?.scenario || undefined,
    researchStrategy,
    searchWeb,
  })

  const running = state.phase === 'running'
  const idle = state.phase === 'idle'
  const outcomeNotice = describeResearchOutcome(state.researchOutcome)

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <Title level={4} style={{ margin: 0 }}>贷前企业尽职调查</Title>
        <Text type="secondary">
          清单驱动 · 证据可溯源 · 规则化评级 · 高风险结论人工复核后方可出具
        </Text>
      </div>

      <Card size="small" className={styles.launcher}>
        <Space wrap style={{ marginBottom: 12 }}>
          <Select
            aria-label="调查模式"
            value={researchStrategy}
            disabled={running}
            onChange={setResearchStrategy}
            style={{ width: 220 }}
            options={[
              { value: 'workflow', label: '标准尽调流程' },
              { value: 'agent', label: '自主调查（实验）' },
            ]}
          />
          <Checkbox checked={searchWeb} disabled={running} onChange={e => setSearchWeb(e.target.checked)}>
            启用公开网页检索
          </Checkbox>
          <Text type="secondary">
            本次来源：{[kbName ? '所选资料库' : '', searchWeb ? '公开网页' : '', selectedProfile ? '所选企业档案' : ''].filter(Boolean).join('、') || '尚未选择来源'}
          </Text>
        </Space>
        {researchStrategy === 'agent' && (
          <Alert type="info" showIcon style={{ marginBottom: 12 }}
            message="AI 将根据新发现继续阅读、追查和检查替代解释，最后列出证据与补件要求。"
            description={!kbName && !searchWeb ? '尚未选择资料库或网页来源；没有资料支持的事项只能列为信息缺口。' : '调查循环最多 12 步、4 分钟；后续评分与报告阶段另计。'} />
        )}
        <Row gutter={[16, 12]}>
          <Col xs={24} md={8}>
          <label htmlFor="dd-profile">企业档案（可选）</label>
          <Select
            id="dd-profile"
            allowClear
            value={companyProfileId}
            onChange={setCompanyProfileId}
            disabled={running}
            placeholder="企业档案（可选）"
            style={{ width: '100%' }}
            options={companyProfiles.map(profile => ({
              value: profile.id,
              label: `${profile.name}${profile.scenario === 'factoring' ? ' · 保理' : ''}`,
            }))}
          />
          </Col>
          <Col xs={24} md={8}>
          <label htmlFor="dd-kb">资料库（可选）</label>
          <Select
            id="dd-kb"
            allowClear
            value={kbName}
            onChange={setKbName}
            disabled={running}
            placeholder="资料库（可选）"
            style={{ width: '100%' }}
            options={knowledgeBases.map(kb => ({
              value: kb.name,
              label: `${kb.name}（${kb.document_count}份）`,
            }))}
          />
          </Col>
          <Col xs={24} md={8}>
          <label htmlFor="dd-asof">调查截止日（可选）</label>
          <Input
            id="dd-asof"
            type="date"
            value={asOf}
            onChange={e => setAsOf(e.target.value)}
            disabled={running}
          />
          </Col>
          <Col span={24}>
          <label htmlFor="dd-subject">调查主体（必填）</label>
          <Input
            id="dd-subject"
            value={selectedProfile?.name || subjectName}
            onChange={e => setSubjectName(e.target.value)}
            placeholder="例如：海岫精密部件有限公司"
            disabled={running || Boolean(companyProfileId)}
          />
          {companyProfileId && <Text type="secondary">主体与所选档案保持一致；如需手动修改，请先清除企业档案选择。调查问题不会被覆盖。</Text>}
          </Col>
          <Col span={24}>
          <label htmlFor="dd-query">调查问题与关注重点（必填）</label>
          <Input.TextArea
            id="dd-query"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="填写具体调查问题、申请金额与期限、需要验证的解释及补件要求。可直接粘贴测试计划中的完整 Q1。"
            autoSize={{ minRows: 4, maxRows: 12 }}
            disabled={running}
          />
          <Text type="secondary">主体与问题分别提交；请确认问题中提到的企业与调查主体一致。回车换行，点击按钮发起。</Text>
          </Col>
          <Col span={24}>
          <Space>
          <Button type="primary" loading={running} disabled={!canLaunch}
            onClick={launch}>
            发起尽调
          </Button>
          {!idle && <Button onClick={reset} disabled={running}>重置</Button>}
          </Space>
          </Col>
        </Row>
        {idle && (
          <Space size={4} wrap style={{ marginTop: 8 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>示例：</Text>
            {SAMPLES.map(s => (
              <Tag key={s} style={{ cursor: companyProfileId ? 'not-allowed' : 'pointer' }} onClick={() => {
                if (companyProfileId) return
                setSubjectName(s.slice(2, s.indexOf('做')))
                setQuery(s)
              }}>
                {s.slice(2, s.indexOf('做'))}
              </Tag>
            ))}
          </Space>
        )}
      </Card>

      {state.phase === 'error' && (
        <Alert type="error" showIcon message="尽调未完成" description={state.errorMessage}
          style={{ marginBottom: 12 }} />
      )}
      {state.phase === 'cancelled' && (
        <Alert type="warning" showIcon message="本次尽调已取消" style={{ marginBottom: 12 }} />
      )}

      {/* 证据链降级必须呈现给复核人：只写进日志的话，
          没人会知道这份结论建立在来源不明的数据上 */}
      {state.errors.length > 0 && (
        <Alert
          type="warning" showIcon style={{ marginBottom: 12 }}
          message={`执行过程中有 ${state.errors.length} 条需关注的记录`}
          description={<ul style={{ margin: 0, paddingLeft: 18 }}>
            {state.errors.map((e, i) => <li key={i}>{e}</li>)}
          </ul>}
        />
      )}

      {idle ? (
        <Empty
          className={styles.empty}
          description={
            <Space direction="vertical" size={2}>
              <Text>输入一家企业发起尽调</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                可直接使用已有企业档案，也可选择资料库检索已上传的文本型 PDF；未核实项不会被写成“无风险”
              </Text>
            </Space>
          }
        />
      ) : (
        <Row gutter={12} className={styles.body}>
          <Col span={24}>
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              {running && (
                <Card size="small">
                  <Space><Spin size="small" /><Text>{state.stage || '进行中…'}</Text></Space>
                  {state.companyName && (
                    <div style={{ marginTop: 6 }}>
                      <Text type="secondary">尽调对象：{state.companyName}</Text>
                    </div>
                  )}
                </Card>
              )}
              {state.profileRef && (
                <Tooltip title={`来源：${state.profileRef.source}\n档案 ID：${state.profileRef.id}\n内容哈希：${state.profileRef.content_sha256}`}>
                  <Tag color="blue">
                    {state.profileRef.source === 'admin_company_profile' ? '管理员档案' : state.profileRef.source}
                    {' · '}修订 r{state.profileRef.revision}
                  </Tag>
                </Tooltip>
              )}
            </Space>
          </Col>

          <Col span={24}>
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              {(state.researchOutcome || state.risk || state.report) && (
                <Alert
                  type={outcomeNotice.type} showIcon
                  message={outcomeNotice.title}
                  description={
                    <Space direction="vertical" size={6} style={{ width: '100%' }}>
                      <Text>{outcomeNotice.explanation}</Text>
                      <Space wrap size={4}>
                        {outcomeNotice.dimensions.map(item => <Tag key={item}>{item}</Tag>)}
                      </Space>
                      {Boolean(state.researchOutcome?.restriction_reasons.length) && (
                        <ul style={{ margin: 0, paddingLeft: 18 }}>
                          {state.researchOutcome?.restriction_reasons.map((reason, index) => <li key={index}>{reason}</li>)}
                        </ul>
                      )}
                    </Space>
                  }
                />
              )}
              {state.phase === 'awaiting_review' && state.reviewRequest && (
                <ReviewCard req={state.reviewRequest} onSubmit={review} submitting={false} />
              )}
              <RiskCard risk={state.risk} />
              {/* 调查层排在评级之后、报告正文之前，与报告里的章节顺序一致：
                  A 层裁决 → B 层调查 → 证据附录。两处顺序必须相同，
                  否则同一份结论在界面上与在报告里读起来是两个东西 */}
              <InvestigationPanel data={state.investigation} />
              {state.agentNotebook && (
                <Card size="small" title="AI 调查发现与补件">
                  <Space direction="vertical" style={{ width: '100%' }}>
                    <Text type="secondary">以下是带原文依据的 AI 分析，不等于事实已独立核实，也不替代规则评级。</Text>
                    <Tag>{({ running: '调查中', completed: '调查结束', step_limit: '已达步骤上限', time_limit: '已达时间上限', stalled: '连续未取得进展' } as Record<string, string>)[state.agentNotebook.status] || state.agentNotebook.status}</Tag>
                    {state.agentNotebook.findings.map((finding, i) => (
                      <div key={`${finding.source_id}-${i}`}>
                        <Text strong>{({ support: '支持线索', counter: '反证 / 替代解释', gap: '信息缺口' } as Record<string, string>)[finding.kind] || '调查发现'}：{finding.claim}</Text>
                        <div><Text>原文：{finding.quote}</Text></div>
                        <Text type="secondary">{finding.title} · {finding.source_id}</Text>
                      </div>
                    ))}
                    {state.agentNotebook.summary && <Text>{state.agentNotebook.summary}</Text>}
                    {(state.agentNotebook.missing_materials.length > 0 ? state.agentNotebook.missing_materials : state.agentNotebook.questions).map((item, i) => (
                      <div key={i}><Text type="warning">待解决：{item}</Text></div>
                    ))}
                  </Space>
                </Card>
              )}
              <CompletenessBar data={state.completeness} />
              <ChecklistTable checks={state.fieldChecks} />
              {state.report && (
                <Card size="small" title={`尽职调查报告 · ${outcomeNotice.title}`} className={styles.report}>
                  <Text type="secondary">{outcomeNotice.explanation}</Text>
                  {/* gfm 必须开启：报告里的评级块、额度测算与溯源附录都是表格 */}
                  <Markdown className={styles.markdown} value={state.report} gfm />
                </Card>
              )}
              {state.steps.length > 0 && (
                <Card size="small" title="执行过程">
                  {state.steps.length === 0
                    ? <Text type="secondary">等待首个步骤…</Text>
                    : <ul style={{ margin: 0, paddingLeft: 18 }}>
                        {state.steps.slice(-12).map((s, i) => (
                          <li key={i}><Text>{s.title}</Text>
                            {s.detail && <Text type="secondary"> · {s.detail}</Text>}</li>
                        ))}
                      </ul>}
                </Card>
              )}
            </Space>
          </Col>
        </Row>
      )}
    </div>
  )
}
