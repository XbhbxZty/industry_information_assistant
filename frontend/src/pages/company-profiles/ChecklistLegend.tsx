import type { CompanyProfileTemplate, ScenarioKey, TemplateField } from '@/api/company-profiles'
import { Alert, Card, Empty, Space, Tag, Typography } from 'antd'

const { Text } = Typography

function FieldTags({ items, scenario }: { items: TemplateField[]; scenario?: boolean }) {
  if (!items.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无清单项" />
  return (
    <Space size={[6, 6]} wrap>
      {items.map(item => (
        <Tag key={item.field_id} color={scenario ? 'purple' : item.required ? 'blue' : 'default'}>
          {item.field_name}{!scenario && item.required ? ' · 尽调必查' : ''}
        </Tag>
      ))}
    </Space>
  )
}

export function ChecklistLegend({ template, scenario }: { template: CompanyProfileTemplate | null; scenario: ScenarioKey }) {
  const scenarioItems = scenario ? template?.scenarios?.[scenario] || [] : []
  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="“尽调必查”是尽调模板的核实要求，不等于创建企业档案时必须填写。"
        description="企业名称是本表单唯一的创建必填项。未录入的数据会在后续尽调中明确显示为信息缺口，不会被写成“无风险”。"
      />
      <Card size="small" title="核心主体清单">
        {!template ? <Text type="secondary">正在加载尽调模板…</Text> : <FieldTags items={template.core} />}
      </Card>
      {scenario && (
        <Card size="small" title={`业务场景清单 · ${scenario}`}>
          {!template ? <Text type="secondary">正在加载尽调模板…</Text> : <FieldTags items={scenarioItems} scenario />}
        </Card>
      )}
    </Space>
  )
}
