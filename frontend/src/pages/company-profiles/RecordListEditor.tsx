import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Card, Form, Input, InputNumber, Select, Space } from 'antd'

type ValueType = 'text' | 'number' | 'date' | 'select'

export interface RecordColumn {
  name: string
  label: string
  type?: ValueType
  required?: boolean
  min?: number
  max?: number
  options?: { label: string; value: string }[]
  placeholder?: string
}

interface RecordListEditorProps {
  name: (string | number)[]
  title: string
  addLabel: string
  columns: RecordColumn[]
  defaultValue?: Record<string, unknown>
}

/** 可复用的结构化记录编辑器；每一行的校验是记录结构约束，不是尽调必查。 */
export function RecordListEditor({ name, title, addLabel, columns, defaultValue }: RecordListEditorProps) {
  return (
    <Card size="small" title={title} className="record-list-card">
      <Form.List name={name}>
        {(fields, { add, remove }) => (
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            {fields.map(field => (
              <Card
                key={field.key}
                size="small"
                type="inner"
                title={`${title} #${field.name + 1}`}
                extra={<Button type="text" danger icon={<DeleteOutlined />} onClick={() => remove(field.name)}>删除</Button>}
              >
                <div className="record-list-grid">
                  {columns.map(column => (
                    <Form.Item
                      key={column.name}
                      label={column.label}
                      name={[field.name, column.name]}
                      rules={column.required ? [{ required: true, message: `请填写${column.label}` }] : undefined}
                    >
                      {column.type === 'number' ? (
                        <InputNumber min={column.min} max={column.max} style={{ width: '100%' }} placeholder={column.placeholder} />
                      ) : column.type === 'select' ? (
                        <Select options={column.options} placeholder={column.placeholder || `请选择${column.label}`} />
                      ) : (
                        <Input placeholder={column.placeholder || (column.type === 'date' ? 'YYYY-MM-DD' : `请输入${column.label}`)} />
                      )}
                    </Form.Item>
                  ))}
                </div>
              </Card>
            ))}
            <Button type="dashed" icon={<PlusOutlined />} onClick={() => add(defaultValue)} block>{addLabel}</Button>
          </Space>
        )}
      </Form.List>
    </Card>
  )
}
