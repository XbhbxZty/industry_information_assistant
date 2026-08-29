import type { CompanyProfileDraft, CompanyProfileTemplate, FieldSource, ScenarioKey } from '@/api/company-profiles'
import { PlusOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Form, Input, InputNumber, Select, Space, Tabs } from 'antd'
import { useMemo } from 'react'
import { ChecklistLegend } from './ChecklistLegend'
import { RecordListEditor, type RecordColumn } from './RecordListEditor'

export type CompanyProfileFormValues = CompanyProfileDraft & { change_reason?: string }

interface ProfileFormProps {
  form: ReturnType<typeof Form.useForm<CompanyProfileFormValues>>[0]
  template: CompanyProfileTemplate | null
  editing: boolean
  submitting: boolean
  onSubmit: (values: CompanyProfileFormValues) => void
}

const shareholderColumns: RecordColumn[] = [
  { name: 'name', label: '股东名称', required: true },
  { name: 'type', label: '股东类型' },
  { name: 'ratio', label: '持股比例（0–1）', type: 'number', required: true, min: 0, max: 1 },
  { name: 'subscribed_capital', label: '认缴出资额' },
]

const financialColumns: RecordColumn[] = [
  { name: 'period', label: '报告期', required: true, placeholder: '例如 2025年度' },
  { name: 'revenue', label: '营业收入', type: 'number' },
  { name: 'net_profit', label: '净利润', type: 'number' },
  { name: 'total_assets', label: '总资产', type: 'number', min: 0 },
  { name: 'total_liabilities', label: '总负债', type: 'number', min: 0 },
  { name: 'debt_ratio', label: '资产负债率（0–1）', type: 'number', min: 0, max: 1 },
  { name: 'operating_cash_flow', label: '经营性现金流', type: 'number' },
  { name: 'unit', label: '单位', placeholder: '万元' },
  { name: 'data_source', label: '数据来源' },
]

const judicialColumns: RecordColumn[] = [
  {
    name: 'type', label: '记录类型', type: 'select', required: true,
    options: ['涉诉', '被执行', '失信', '股权冻结', '行政处罚'].map(value => ({ label: value, value })),
  },
  { name: 'case_no', label: '案号', required: true },
  { name: 'role', label: '当事人角色' },
  { name: 'cause', label: '案由' },
  { name: 'amount', label: '涉案金额', type: 'number', min: 0 },
  { name: 'filing_date', label: '立案日期', type: 'date' },
  { name: 'status', label: '进展状态' },
]

const guaranteeColumns: RecordColumn[] = [
  { name: 'beneficiary', label: '被担保方', required: true },
  { name: 'guarantee_type', label: '担保方式', required: true },
  { name: 'amount', label: '担保金额', type: 'number', required: true, min: 0 },
  { name: 'period', label: '担保期限' },
  { name: 'board_resolution', label: '内部决议情况' },
]

const relationColumns: RecordColumn[] = [
  { name: 'name', label: '关联方名称', required: true },
  { name: 'relation', label: '关联关系', required: true },
  { name: 'transaction', label: '交易/资金占用说明' },
]

function isIsoDateOrDateTime(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:T(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,6})?)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?)?$/.exec(value)
  if (!match) return false
  const [, year, month, day] = match.map(Number)
  const parsed = new Date(Date.UTC(year, month - 1, day))
  return parsed.getUTCFullYear() === year
    && parsed.getUTCMonth() === month - 1
    && parsed.getUTCDate() === day
}

function isoDateValidator(label: string) {
  return (_: unknown, value: string | undefined) => (
    !value || isIsoDateOrDateTime(value.trim())
      ? Promise.resolve()
      : Promise.reject(new Error(`${label}必须是 ISO 日期或时间`))
  )
}

export function ProfileForm({ form, template, editing, submitting, onSubmit }: ProfileFormProps) {
  const scenario = (Form.useWatch('scenario', form) || '') as ScenarioKey
  const scenarioTemplateFields = useMemo(() => scenario ? template?.scenarios?.[scenario] || [] : [], [scenario, template])
  const scenarioOptions = useMemo(() => {
    const values = new Set<ScenarioKey>([
      '',
      ...(template?.scenario_options || []),
      ...Object.keys(template?.scenarios || {}),
    ])
    return Array.from(values, value => ({ label: value || '通用企业尽调', value }))
  }, [template])
  const sourceOptions = useMemo(() => {
    const core = template?.core || []
    return [...core, ...scenarioTemplateFields].map(item => ({ label: item.field_name, value: item.field_id }))
  }, [scenarioTemplateFields, template])

  return (
    <Form<CompanyProfileFormValues>
      form={form}
      layout="vertical"
      requiredMark="optional"
      onFinish={onSubmit}
      initialValues={{ scenario: '', scenario_data: {}, field_sources: [], materials: [] }}
    >
      <Tabs
        items={[
          {
            key: 'subject', label: '主体与场景', children: (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <Card size="small" title="企业主体">
                  <div className="profile-form-grid">
                    <Form.Item name={['profile', 'name']} label="企业名称" rules={[{ required: true, whitespace: true, message: '企业名称是创建档案的唯一必填项' }]}>
                      <Input placeholder="请输入企业全称" />
                    </Form.Item>
                    <Form.Item name={['profile', 'credit_code']} label="统一社会信用代码" rules={[{ pattern: /^[0-9A-Z]{18}$/, message: '请输入 18 位大写字母或数字' }]}>
                      <Input maxLength={18} placeholder="可稍后补充" onChange={event => form.setFieldValue(['profile', 'credit_code'], event.target.value.toUpperCase())} />
                    </Form.Item>
                    <Form.Item name="scenario" label="业务场景">
                      <Select
                        options={scenarioOptions}
                        onChange={(nextScenario: ScenarioKey) => {
                          const allowedFieldIds = new Set([
                            ...(template?.core || []).map(field => field.field_id),
                            ...(template?.scenarios?.[nextScenario] || []).map(field => field.field_id),
                          ])
                          form.setFieldValue('scenario', nextScenario)
                          form.setFieldValue('scenario_data', {})
                          const existingSources = (form.getFieldValue('field_sources') || []) as FieldSource[]
                          form.setFieldValue('field_sources', existingSources.map(source => ({
                            ...source,
                            field_ids: source.field_ids.filter(fieldId => allowedFieldIds.has(fieldId)),
                          })))
                        }}
                      />
                    </Form.Item>
                  </div>
                </Card>
                <ChecklistLegend template={template} scenario={scenario} />
                {scenario && (
                  <Card size="small" title="场景数据">
                    <Alert type="info" showIcon message="场景数据在尽调时单独统计，不会改变核心主体清单的核实率或风险评级。" style={{ marginBottom: 12 }} />
                    <div className="profile-form-grid">
                      {scenarioTemplateFields.map(field => (
                        <Form.Item
                          key={field.field_id}
                          name={['scenario_data', field.field_id]}
                          label={field.field_name}
                          extra={field.description}
                          dependencies={['field_sources']}
                          rules={[({ getFieldValue }) => ({
                            validator: (_, value: string | number | null | undefined) => {
                              const hasValue = value !== null && value !== undefined
                                && (typeof value !== 'string' || Boolean(value.trim()))
                              if (!hasValue) return Promise.resolve()
                              const sources = (getFieldValue('field_sources') || []) as FieldSource[]
                              return sources.some(source => source.field_ids?.includes(field.field_id))
                                ? Promise.resolve()
                                : Promise.reject(new Error('填写场景值前，必须在“来源与材料”中添加覆盖该字段的来源'))
                            },
                          })]}
                        >
                          {field.input_type === 'number'
                            ? <InputNumber style={{ width: '100%' }} placeholder="可稍后补充" />
                            : <Input.TextArea autoSize={{ minRows: 1, maxRows: 3 }} placeholder="可稍后补充" />}
                        </Form.Item>
                      ))}
                    </div>
                  </Card>
                )}
              </Space>
            ),
          },
          {
            key: 'identity', label: '工商与股权', children: (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <Card size="small" title="工商登记">
                  <div className="profile-form-grid">
                    {[
                      ['registered_capital', '注册资本'], ['paid_in_capital', '实缴资本'], ['established_date', '成立日期'],
                      ['legal_representative', '法定代表人'], ['company_type', '企业类型'], ['registered_address', '注册地址'], ['operating_status', '登记状态'],
                    ].map(([field, label]) => (
                      <Form.Item key={field} name={['profile', 'registration', field]} label={label}>
                        <Input placeholder={field === 'established_date' ? 'YYYY-MM-DD' : '可稍后补充'} />
                      </Form.Item>
                    ))}
                    <Form.Item name={['profile', 'registration', 'business_scope']} label="经营范围" className="profile-form-grid__wide">
                      <Input.TextArea rows={3} placeholder="可稍后补充" />
                    </Form.Item>
                  </div>
                </Card>
                <RecordListEditor name={['profile', 'shareholders']} title="股东" addLabel="添加股东" columns={shareholderColumns} />
                <Card size="small" title="实际控制人">
                  <div className="profile-form-grid">
                    <Form.Item name={['profile', 'actual_controller', 'name']} label="姓名/名称"><Input /></Form.Item>
                    <Form.Item name={['profile', 'actual_controller', 'data_source']} label="认定来源"><Input /></Form.Item>
                    <Form.Item name={['profile', 'actual_controller', 'basis']} label="认定依据" className="profile-form-grid__wide"><Input.TextArea rows={3} /></Form.Item>
                  </div>
                </Card>
              </Space>
            ),
          },
          {
            key: 'financial', label: '财务与业务', children: (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <RecordListEditor name={['profile', 'financials']} title="财务期间记录" addLabel="添加财务期间" columns={financialColumns} defaultValue={{ unit: '万元' }} />
                <Card size="small" title="授信申请">
                  <div className="profile-form-grid">
                    <Form.Item name={['profile', 'credit_application', 'product']} label="授信产品"><Input /></Form.Item>
                    <Form.Item name={['profile', 'credit_application', 'amount']} label="申请金额"><InputNumber min={0} style={{ width: '100%' }} /></Form.Item>
                    <Form.Item name={['profile', 'credit_application', 'unit']} label="金额单位"><Input placeholder="万元" /></Form.Item>
                    <Form.Item name={['profile', 'credit_application', 'term']} label="授信期限"><Input placeholder="例如 12个月" /></Form.Item>
                    <Form.Item name={['profile', 'credit_application', 'purpose']} label="资金用途" className="profile-form-grid__wide"><Input.TextArea rows={3} /></Form.Item>
                  </div>
                </Card>
              </Space>
            ),
          },
          {
            key: 'risk', label: '司法、担保与关联', children: (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <RecordListEditor name={['profile', 'judicial_records']} title="司法记录" addLabel="添加司法记录" columns={judicialColumns} />
                <RecordListEditor name={['profile', 'guarantee']} title="对外担保" addLabel="添加担保记录" columns={guaranteeColumns} />
                <RecordListEditor name={['profile', 'related_party']} title="关联方" addLabel="添加关联方" columns={relationColumns} />
              </Space>
            ),
          },
          {
            key: 'sources', label: '来源与材料', children: (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <Alert type="warning" showIcon message="来源记录用于声明字段的取证依据。补充材料只供人工阅读与检索，不能作为结构化证据，也不会自动进入风险评分。" />
                <Card size="small" title="字段来源记录">
                  <Form.List name="field_sources">
                    {(fields, { add, remove }) => (
                      <Space direction="vertical" size={8} style={{ width: '100%' }}>
                        {fields.map(field => (
                          <Card key={field.key} size="small" type="inner" title={`来源 #${field.name + 1}`} extra={<Button type="text" danger onClick={() => remove(field.name)}>删除</Button>}>
                            <div className="profile-form-grid">
                              <Form.Item name={[field.name, 'field_ids']} label="覆盖字段" rules={[{ required: true, message: '请选择覆盖字段' }]}>
                                <Select mode="multiple" options={sourceOptions} placeholder="选择一个或多个字段" />
                              </Form.Item>
                              <Form.Item name={[field.name, 'source_id']} label="来源 ID" rules={[{ required: true, whitespace: true, message: '请填写来源 ID' }]}>
                                <Input placeholder="例如 registry-2026-001" />
                              </Form.Item>
                              <Form.Item name={[field.name, 'name']} label="来源名称" rules={[{ required: true, whitespace: true, message: '请填写来源名称' }]}>
                                <Input placeholder="例如 企业信用信息公示系统" />
                              </Form.Item>
                              <Form.Item name={[field.name, 'issuer']} label="出具机构" rules={[{ required: true, whitespace: true, message: '请填写出具机构' }]}>
                                <Input />
                              </Form.Item>
                              <Form.Item name={[field.name, 'source_type']} label="来源类型" rules={[{ required: true, message: '请选择来源类型' }]}>
                                <Select options={[{ value: 'official', label: '官方' }, { value: 'authorized', label: '授权' }, { value: 'audited', label: '审计' }]} />
                              </Form.Item>
                              <Form.Item name={[field.name, 'retrieved_at']} label="获取时间" rules={[{ required: true, whitespace: true, message: '请填写获取时间' }, { validator: isoDateValidator('获取时间') }]}>
                                <Input placeholder="ISO 日期或时间，例如 2026-08-28T09:30:00" />
                              </Form.Item>
                              <Form.Item name={[field.name, 'as_of_date']} label="数据截止日" rules={[{ required: true, whitespace: true, message: '请填写数据截止日' }, { validator: isoDateValidator('数据截止日') }]}>
                                <Input placeholder="ISO 日期或时间，例如 2026-08-28" />
                              </Form.Item>
                              <Form.Item name={[field.name, 'reference']} label="引用/说明" rules={[{ required: true, whitespace: true, message: '请填写可追溯引用' }]}>
                                <Input />
                              </Form.Item>
                              <Form.Item name={[field.name, 'sha256']} label="SHA-256（可选）" rules={[{ pattern: /^[0-9a-fA-F]{64}$/, message: '请输入 64 位十六进制摘要' }]}>
                                <Input placeholder="64 位十六进制摘要" />
                              </Form.Item>
                            </div>
                          </Card>
                        ))}
                        <Button type="dashed" icon={<PlusOutlined />} onClick={() => add()} block>添加来源记录</Button>
                      </Space>
                    )}
                  </Form.List>
                </Card>
                <Card size="small" title="补充材料">
                  <Form.List name="materials">
                    {(fields, { add, remove }) => (
                      <Space direction="vertical" size={8} style={{ width: '100%' }}>
                        {fields.map(field => (
                          <Card key={field.key} size="small" type="inner" title={`材料 #${field.name + 1}`} extra={<Button type="text" danger onClick={() => remove(field.name)}>删除</Button>}>
                            <div className="profile-form-grid">
                              <Form.Item name={[field.name, 'material_id']} label="材料 ID（可选）"><Input placeholder="留空由服务端生成" /></Form.Item>
                              <Form.Item name={[field.name, 'source_type']} label="材料来源" rules={[{ required: true, message: '请选择材料来源' }]}>
                                <Select options={[{ value: 'company_submitted', label: '企业提交' }, { value: 'admin_observation', label: '管理员观察' }]} />
                              </Form.Item>
                              <Form.Item name={[field.name, 'title']} label="材料标题" rules={[{ required: true, whitespace: true, message: '请填写材料标题' }]}><Input /></Form.Item>
                              <Form.Item name={[field.name, 'reference']} label="引用/说明（可选）"><Input /></Form.Item>
                              <Form.Item
                                name={[field.name, 'as_of_date']}
                                label="材料日期"
                                dependencies={[["materials", field.name, 'date_unknown_reason']]}
                                rules={[({ getFieldValue }) => ({
                                  validator: async (_: unknown, value: string | undefined) => {
                                    const unknownReason = getFieldValue(['materials', field.name, 'date_unknown_reason'])
                                    if (Boolean(value?.trim()) === Boolean(unknownReason?.trim())) {
                                      throw new Error('材料日期与未知日期原因必须且只能填写一项')
                                    }
                                    if (value && !isIsoDateOrDateTime(value.trim())) {
                                      throw new Error('材料日期必须是 ISO 日期或时间')
                                    }
                                  },
                                })]}
                              ><Input placeholder="ISO 日期或时间，例如 2026-08-28" /></Form.Item>
                              <Form.Item
                                name={[field.name, 'date_unknown_reason']}
                                label="未知日期原因"
                                dependencies={[["materials", field.name, 'as_of_date']]}
                                rules={[({ getFieldValue }) => ({
                                  validator: (_, value: string | undefined) => {
                                    const asOfDate = getFieldValue(['materials', field.name, 'as_of_date'])
                                    return Boolean(value?.trim()) !== Boolean(asOfDate?.trim())
                                      ? Promise.resolve()
                                      : Promise.reject(new Error('材料日期与未知日期原因必须且只能填写一项'))
                                  },
                                })]}
                              ><Input placeholder="例如：企业未提供材料形成日期" /></Form.Item>
                              <Form.Item name={[field.name, 'content']} label="补充文本" className="profile-form-grid__wide" rules={[{ required: true, whitespace: true, message: '请填写补充文本' }]}><Input.TextArea rows={5} /></Form.Item>
                            </div>
                          </Card>
                        ))}
                        <Button type="dashed" icon={<PlusOutlined />} onClick={() => add({ source_type: 'company_submitted' })} block>添加补充材料</Button>
                      </Space>
                    )}
                  </Form.List>
                </Card>
                {editing && <Form.Item name="change_reason" label="本次修改说明" rules={[{ required: true, whitespace: true, message: '请填写本次修改说明' }]}><Input.TextArea rows={2} placeholder="必须填写，便于历史追溯" /></Form.Item>}
              </Space>
            ),
          },
        ]}
      />
      <div className="profile-form-actions">
        <Button type="primary" htmlType="submit" loading={submitting}>{editing ? '保存修改' : '创建档案'}</Button>
      </div>
    </Form>
  )
}
