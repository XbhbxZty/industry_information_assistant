// Copyright © 2026 XbhbxZty
// 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
import { useState } from 'react'
import { Alert, Button, Card, Col, Empty, Input, Row, Space, Spin, Tag, Typography } from 'antd'
import Markdown from '@/components/markdown'
import { ChecklistTable, CompletenessBar, ReviewCard, RiskCard } from './components'
import { useDDStream } from './useDDStream'
import styles from './index.module.scss'

const { Title, Text } = Typography

const SAMPLES = [
  '请对东莞市泰锐精密传动件有限公司做贷前尽职调查，授信2000万元',
  '请对郑州宏川物流供应链有限公司做贷前尽职调查，授信3000万元',
  '请对苏州恒晟自动化设备有限公司做贷前尽职调查，授信5000万元',
]

export default function DueDiligencePage() {
  const { state, start, review, reset } = useDDStream()
  const [query, setQuery] = useState(SAMPLES[0])

  const running = state.phase === 'running'
  const idle = state.phase === 'idle'

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <Title level={4} style={{ margin: 0 }}>贷前企业尽职调查</Title>
        <Text type="secondary">
          清单驱动 · 证据可溯源 · 规则化评级 · 高风险结论人工复核后方可出具
        </Text>
      </div>

      <Card size="small" className={styles.launcher}>
        <Space.Compact style={{ width: '100%' }}>
          <Input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="输入尽调对象与授信申请，例如：请对某某有限公司做贷前尽职调查，授信2000万元"
            onPressEnter={() => !running && query.trim() && start(query)}
            disabled={running}
          />
          <Button type="primary" loading={running} disabled={!query.trim()}
            onClick={() => start(query)}>
            发起尽调
          </Button>
          {!idle && <Button onClick={reset} disabled={running}>重置</Button>}
        </Space.Compact>
        {idle && (
          <Space size={4} wrap style={{ marginTop: 8 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>示例：</Text>
            {SAMPLES.map(s => (
              <Tag key={s} style={{ cursor: 'pointer' }} onClick={() => setQuery(s)}>
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
                数据源为虚构的模拟数据，无需任何付费 Key 即可跑通
              </Text>
            </Space>
          }
        />
      ) : (
        <Row gutter={12} className={styles.body}>
          <Col span={10}>
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
              <CompletenessBar data={state.completeness} />
              <ChecklistTable checks={state.fieldChecks} />
            </Space>
          </Col>

          <Col span={14}>
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              {state.phase === 'awaiting_review' && state.reviewRequest && (
                <ReviewCard req={state.reviewRequest} onSubmit={review} submitting={false} />
              )}
              <RiskCard risk={state.risk} />
              {state.report && (
                <Card size="small" title="尽职调查报告" className={styles.report}>
                  {/* gfm 必须开启：报告里的评级块、额度测算与溯源附录都是表格 */}
                  <Markdown className={styles.markdown} value={state.report} gfm />
                </Card>
              )}
              {running && !state.risk && (
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
