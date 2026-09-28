import { Alert, Card, Collapse, Space, Tag, Typography } from 'antd'
import type { AgentNotebook, CalculationWorkpaper, MaterialDocument } from './agent-notebook'

const { Text } = Typography

const STATUS = {
  not_started: '尚未开始', running: '调查中', completed: '调查结束', step_limit: '已达步骤上限',
  time_limit: '已达时间上限', stalled: '连续未取得进展', cancelled: '调查已取消',
}

function materialStatus(document: MaterialDocument) {
  if (document.index_status === 'failed') return '处理失败，不能视为未提供'
  if (document.index_status === 'pending') return '等待处理，材料已登记'
  if (document.index_status === 'processing') return '处理中，材料已登记'
  if (document.index_status === 'unknown') return '索引状态未确认'
  if (!document.read_chunks) return '尚未阅读'
  return document.read_chunks < document.chunk_count ? '已读部分片段' : '已读已知全部片段'
}

function CalculationDetail({ item }: { item: CalculationWorkpaper }) {
  return (
    <Space direction="vertical" size={8} style={{ width: '100%', overflowWrap: 'anywhere' }}>
      <Text>公式：<Text code>{item.expression}</Text></Text>
      <Text>结果：{item.result} {item.result_unit || '（单位未注明）'}</Text>
      <Text type="secondary">已完成算术计算 · 原文未独立核实 · 分析结论待复核</Text>
      {Object.entries(item.variables).map(([name, variable]) => (
        <div key={name}>
          <Text strong>{name} = {variable.value}</Text>
          <div><Text type="secondary">单位：{variable.unit || '未注明'}；期间：{variable.period || '未注明'}；主体：{variable.subject || '未注明'}</Text></div>
          <div><Text>原文：{variable.quote}</Text></div>
          <Text type="secondary">{variable.title || '未命名来源'} · {variable.source_id} / {variable.quote_id}</Text>
        </div>
      ))}
      {item.literal_constants.length > 0 && <Text type="secondary">公式常量：{[...new Set(item.literal_constants)].join('、')}（不是额外事实数据）</Text>}
      <Text>口径与限制：{item.limitations || '未注明；仍须核查主体、期间、单位和分类是否可比。'}</Text>
    </Space>
  )
}

export function AgentWorkbench({ notebook }: { notebook: AgentNotebook | null }) {
  if (!notebook) return null
  const coverage = notebook.material_coverage
  const materialsTitle = coverage?.catalog_status === 'available'
    ? `材料阅读：${coverage.documents_read} / ${coverage.documents_total} 份（至少一片）`
    : '材料目录与阅读范围'
  const catalogNote = !coverage ? '此记录未提供有效的材料目录回执，无法确认阅读覆盖范围。'
    : coverage.catalog_status === 'unavailable' ? '材料目录获取失败，不能据此判定材料未提供。'
      : coverage.catalog_status === 'not_requested' ? '尚未请求材料目录，不能据此判定材料未提供。'
        : coverage.documents_total === 0 ? '当前目录中暂无材料，不代表主体不存在相关信息。' : ''
  const knownCalculations = new Set(notebook.calculations.map(item => item.id))
  return (
    <Card size="small" title="AI 调查工作台">
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Text type="secondary">阅读不等于核实；计算正确不代表来源真实、口径可比或因果解释正确，不替代规则评级与人工判断。</Text>
        <Tag>{STATUS[notebook.status]}</Tag>
        {notebook.display_warnings.length > 0 && <Alert type="warning" showIcon message="部分调查回执未能完整展示"
          description={notebook.display_warnings.map(note => <div key={note}>{note}</div>)} />}
        <Collapse size="small" style={{ width: '100%' }} items={[
          { key: 'materials', label: materialsTitle, children: (
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              {catalogNote && <Text type="warning">{catalogNote}</Text>}
              {coverage && coverage.catalog_status === 'available' && <>
                <Text>实际已读 {coverage.read_chunks} / {coverage.known_chunks} 个已知片段；一份材料读过一片，不代表全文已读。</Text>
                {coverage.truncated && <Text type="warning">目录或片段统计已截断，仅反映当前已知范围，不能据此声称已读完全部资料。</Text>}
              </>}
              {coverage?.documents.map(document => (
                <div key={document.source_id} style={{ overflowWrap: 'anywhere' }}>
                  <Text strong>{document.title}</Text>
                  <div><Tag color={document.index_status === 'failed' ? 'error' : 'default'}>{materialStatus(document)}</Tag>
                    <Text type="secondary">{document.read_chunks} / {document.chunk_count} 个片段 · {document.source_id}</Text></div>
                </div>
              ))}
            </Space>
          ) },
          { key: 'calculations', label: `计算底稿：${notebook.calculations.length} 项`, children: notebook.calculations.length ? (
            <Collapse size="small" items={notebook.calculations.map(item => ({
              key: item.id, label: `${item.id} · ${item.label}：${item.result} ${item.result_unit}`,
              children: <CalculationDetail item={item} />,
            }))} />
          ) : <Text type="secondary">本轮尚无可展示的计算回执；不能仅凭报告中的数字认定已完成工具计算。</Text> },
        ]} />
        {notebook.findings.map((finding, index) => (
          <div key={`${finding.source_id}-${index}`} style={{ overflowWrap: 'anywhere' }}>
            <Text strong>{({ support: '支持线索', counter: '反证 / 替代解释', gap: '信息缺口' } as Record<string, string>)[finding.kind] || '调查发现'}：{finding.claim}</Text>
            <Collapse size="small" style={{ marginTop: 6 }} items={[{ key: 'citations', label: `查看 ${finding.citations.length} 处原文依据`, children: (
              <Space direction="vertical" size={8}>
                {finding.citations.map((origin, position) => (
                  <div key={`${origin.source_id}-${origin.quote_id}-${position}`}>
                    <div><Text>原文：{origin.quote}</Text></div>
                    <Text type="secondary">{origin.title || '未命名来源'} · {origin.source_id}{origin.quote_id ? ` / ${origin.quote_id}` : ''}</Text>
                  </div>
                ))}
              </Space>
            ) }]} />
            {finding.calculation_ids.length > 0 && <Text type="secondary">关联计算：{finding.calculation_ids.map(id => knownCalculations.has(id) ? id : `${id}（回执未展示）`).join('、')}</Text>}
          </div>
        ))}
        {notebook.summary && <Text>{notebook.summary}</Text>}
        {(notebook.missing_materials.length ? notebook.missing_materials : notebook.questions).map((item, index) => (
          <div key={index}><Text type="warning">待解决：{item}</Text></div>
        ))}
      </Space>
    </Card>
  )
}
