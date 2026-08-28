// Copyright © 2026 XbhbxZty
// 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
//
// 调查层（B 层）呈现 —— 双轨产出计划阶段 1
//
// ## 这个面板存在的理由
//
// 尽调模式此前把图表和知识图谱整块关掉了，报告只剩表格，看起来"什么都不敢说"。
// 但「不能给授信结论」和「报告没价值」是两件事——本面板把后者补回来，
// 且不碰任何授信逻辑：这里的数据来自 state.investigation，
// 与 risk / completeness / fieldChecks 在前后端都是分开的两套。
//
// ## 视觉隔离是这个文件最重要的职责
//
// 红线 2：**一张图比一句话权威得多。** 读者会核对正文措辞，
// 但不会去核对趋势图下面的脚注。所以确定性与探索性的区别不能只写在脚注里，
// 必须体现在**图形本身**：
//
//   确定性  实线 + 蓝色 + 实边框 + 「已核实字段」绿标 + 证据编号
//   探索性  虚线 + 橙色 + 虚边框 + 徽标 + 来源与获取时间
//
// 即使读者只瞥一眼、或者把图截下来转发，虚线与橙色也跟着走。
import ReactECharts from 'echarts-for-react'
import { Alert, Card, Empty, Space, Tag, Tooltip, Typography } from 'antd'
import type {
  Investigation, InvestigationChart, InvestigationFailure, InvestigationFinding,
  InvestigationGraph,
} from '@/api/duediligence'

const { Text, Paragraph } = Typography

const DETERMINISTIC_COLOR = '#1677ff'
const EXPLORATORY_COLOR = '#fa8c16'

/** 失败三态。**必须区分**——「查了没有」要不要补查，与「没查成」完全不同 */
const FAILURE_META: Record<InvestigationFailure['kind'], { color: string; label: string }> = {
  not_found: { color: 'default', label: '查了没有' },
  error: { color: 'red', label: '没查成' },
  disabled: { color: 'default', label: '本次未启用' },
}

function isExploratory(chart: InvestigationChart) {
  return chart.chart_class === 'exploratory'
}

// ------------------------------------------------------------------ 图表

function chartOption(chart: InvestigationChart) {
  const exploratory = isExploratory(chart)
  const color = exploratory ? EXPLORATORY_COLOR : DETERMINISTIC_COLOR
  const base = {
    grid: { left: 48, right: 16, top: 24, bottom: 28 },
    tooltip: { trigger: 'axis' as const },
    color: [color],
  }

  if (chart.chart_type === 'graph') {
    const nodes = (chart.graph?.nodes || []) as Record<string, unknown>[]
    const edges = (chart.graph?.edges || []) as Record<string, unknown>[]
    return {
      tooltip: {},
      series: [{
        type: 'graph',
        layout: 'force',
        roam: true,
        label: { show: true, position: 'right', fontSize: 11 },
        force: { repulsion: 220, edgeLength: 110 },
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: 7,
        data: nodes.map(n => ({
          name: String(n.label ?? n.id ?? ''),
          symbolSize: n.is_subject ? 42 : 26,
          // 环上的节点必须自己红起来：这是监管明确关注的系统性风险，
          // 让读者去数箭头方向不现实
          itemStyle: {
            color: n.is_subject ? '#1677ff' : n.on_circle ? '#cf1322'
              : exploratory ? EXPLORATORY_COLOR : '#8c8c8c',
          },
        })),
        links: edges.map(e => ({
          source: String(e.source ?? ''),
          target: String(e.target ?? ''),
          label: { show: true, formatter: String(e.relation ?? ''), fontSize: 10 },
          lineStyle: {
            width: e.on_circle ? 2.5 : 1.2,
            color: e.on_circle ? '#cf1322' : undefined,
            type: exploratory ? 'dashed' : 'solid',
          },
        })),
      }],
    }
  }

  if (chart.chart_type === 'stacked_bar') {
    // 构成图不是时间序列：已核实与信息缺口叠在一根柱子上，
    // 缺口本身才是要看的东西
    return {
      ...base,
      color: ['#52c41a', '#ffd591'],
      legend: { data: ['已核实', '信息缺口'], top: 0, itemHeight: 8, textStyle: { fontSize: 11 } },
      grid: { ...base.grid, top: 32 },
      xAxis: { type: 'category', data: chart.series.map(p => p.period),
        axisLabel: { fontSize: 11 } },
      yAxis: { type: 'value', name: chart.unit, nameTextStyle: { fontSize: 11 } },
      series: [
        { name: '已核实', type: 'bar', stack: 'x', data: chart.series.map(p => p.value) },
        { name: '信息缺口', type: 'bar', stack: 'x', data: chart.series.map(p => p.missing ?? 0) },
      ],
    }
  }

  return {
    ...base,
    xAxis: { type: 'category', data: chart.series.map(p => p.period),
      axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', name: chart.unit, nameTextStyle: { fontSize: 11 } },
    series: [{
      type: chart.chart_type === 'bar' ? 'bar' : 'line',
      smooth: false,
      data: chart.series.map(p => p.value),
      label: { show: true, fontSize: 10, formatter: (o: { dataIndex: number }) =>
        chart.series[o.dataIndex]?.text ?? String(chart.series[o.dataIndex]?.value ?? '') },
      // 探索性折线走虚线：截图转发之后脚注会丢，线型不会
      lineStyle: isExploratory(chart) ? { type: 'dashed', width: 2 } : { width: 2 },
      itemStyle: { color: isExploratory(chart) ? EXPLORATORY_COLOR : DETERMINISTIC_COLOR },
    }],
  }
}

function Provenance({ chart }: { chart: InvestigationChart }) {
  if (!isExploratory(chart)) {
    const ids = chart.provenance.evidence_ids || []
    return (
      <Text type="secondary" style={{ fontSize: 11 }}>
        依据字段：{chart.provenance.field_name}
        {ids.length > 0 && <>{'　'}证据编号：{ids.join('、')}</>}
      </Text>
    )
  }
  const sources = chart.provenance.sources || []
  return (
    <Text type="secondary" style={{ fontSize: 11 }}>
      来源：{sources.map(s => `${s.title || s.url || '未标注'}（${s.published_at || '日期未标注'}）`)
        .join('；') || '未标注'}
    </Text>
  )
}

function ChartBlock({ chart }: { chart: InvestigationChart }) {
  const exploratory = isExploratory(chart)
  return (
    <div
      style={{
        border: `1px ${exploratory ? 'dashed' : 'solid'} ${exploratory ? '#ffd591' : '#e8e8e8'}`,
        background: exploratory ? '#fffbe6' : '#fff',
        borderRadius: 6, padding: '8px 10px', marginBottom: 10,
      }}
    >
      <Space size={6} style={{ marginBottom: 2 }}>
        <Text strong style={{ fontSize: 13 }}>{chart.plain_title}</Text>
        {exploratory
          ? <Tooltip title={chart.note}>
              <Tag color="orange" style={{ marginInlineEnd: 0 }}>探索性 · 未经核实</Tag>
            </Tooltip>
          : <Tooltip title={chart.note}>
              <Tag color="green" style={{ marginInlineEnd: 0 }}>已核实字段</Tag>
            </Tooltip>}
        {chart.subtitle && (
          <Text type="secondary" style={{ fontSize: 11 }}>{chart.subtitle}</Text>
        )}
      </Space>
      <ReactECharts
        option={chartOption(chart)}
        style={{ height: chart.chart_type === 'graph' ? 260 : 190 }}
        notMerge
      />
      <Provenance chart={chart} />
    </div>
  )
}

// ------------------------------------------------------------ 实体关系图谱

/** 探索性关系图谱。与担保圈图谱刻意长得不一样：那张是确定性的、每条边
 *  有金额与证据编号；这张是模型抽的，只能是虚线橙色，且每条边要能点出来源。
 *  收集了却不渲染就是「接了一半的线」——数据在 state 里，读者看不见。 */
function RelationGraph({ graph }: { graph: InvestigationGraph }) {
  if (!graph?.edges?.length) return null
  const option = {
    tooltip: {
      formatter: (p: { dataType: string; data: { origin?: { publisher?: string; title?: string; published_at?: string } } }) => {
        if (p.dataType !== 'edge') return ''
        const o = p.data.origin || {}
        return `来源：${o.publisher || o.title || '未标注'}<br/>发布：${o.published_at || '未标注'}`
      },
    },
    series: [{
      type: 'graph', layout: 'force', roam: true,
      label: { show: true, position: 'right', fontSize: 11 },
      force: { repulsion: 220, edgeLength: 110 },
      edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 7,
      data: graph.nodes.map(n => ({
        name: n.label || n.id,
        symbolSize: n.is_subject ? 42 : 26,
        itemStyle: { color: n.is_subject ? DETERMINISTIC_COLOR : EXPLORATORY_COLOR },
      })),
      links: graph.edges.map(e => ({
        source: e.source, target: e.target, origin: e.origin,
        label: { show: true, formatter: e.relation, fontSize: 10 },
        lineStyle: { type: 'dashed', width: 1.2 },
      })),
    }],
  }
  const sourced = graph.edges.filter(e => e.origin?.publisher || e.origin?.title).length
  return (
    <div style={{
      border: '1px dashed #ffd591', background: '#fffbe6',
      borderRadius: 6, padding: '8px 10px', marginBottom: 10,
    }}>
      <Space size={6} style={{ marginBottom: 2 }}>
        <Text strong style={{ fontSize: 13 }}>实体关系图谱</Text>
        <Tag color="orange" style={{ marginInlineEnd: 0 }}>探索性 · 未经核实</Tag>
        <Text type="secondary" style={{ fontSize: 11 }}>
          节点 {graph.nodes.length}{'　'}关系 {graph.edges.length}
        </Text>
      </Space>
      <ReactECharts option={option} style={{ height: 260 }} notMerge />
      <Text type="secondary" style={{ fontSize: 11 }}>
        {sourced}/{graph.edges.length} 条关系可指认来源（悬停查看）；
        本图不做实体消歧，同名主体会被合并，不得作为授信依据
      </Text>
    </div>
  )
}

// ------------------------------------------------------------------ 发现

function FindingList({ findings }: { findings: InvestigationFinding[] }) {
  if (findings.length === 0) return null
  return (
    <div style={{ marginTop: 4 }}>
      {findings.map((f, i) => (
        <div key={i} style={{
          borderLeft: `3px solid ${EXPLORATORY_COLOR}`, background: '#fffbe6',
          padding: '6px 10px', marginBottom: 6, borderRadius: 4,
        }}>
          <Space size={6} wrap>
            <Tag color="orange" style={{ marginInlineEnd: 0 }}>未经核实</Tag>
            <Text style={{ fontSize: 13 }}>{f.claim}</Text>
          </Space>
          <div>
            <Text type="secondary" style={{ fontSize: 11 }}>
              来源：{f.source.publisher || f.source.title || '未标注'}
              {'　'}发布：{f.source.published_at || '未标注'}
              {'　'}获取：{(f.source.retrieved_at || '未标注').slice(0, 19).replace('T', ' ')}
            </Text>
          </div>
        </div>
      ))}
    </div>
  )
}

// ------------------------------------------------------------------ 面板

export function InvestigationPanel({ data }: { data: Investigation | null }) {
  if (!data) return null

  const deterministic = data.charts.filter(c => !isExploratory(c))
  const exploratory = data.charts.filter(isExploratory)
  const hasAnything = data.charts.length > 0 || data.findings.length > 0
    || (data.graph?.edges?.length ?? 0) > 0
  if (!hasAnything && data.failures.length === 0) return null

  return (
    <Card size="small" title="调查层补充"
      extra={<Text type="secondary" style={{ fontSize: 11 }}>不参与授信裁决</Text>}>
      {/* 这句声明必须在最上面。B 层内容比清单好读，读者会先看到它；
          不先声明，它就会被当成结论的一部分（隔离要求 1） */}
      <Alert
        type="info" showIcon style={{ marginBottom: 10 }}
        message="本区内容不进入风险评级、授信额度与证据溯源附录"
        description={
          <Text style={{ fontSize: 12 }}>
            带<Tag color="green" style={{ margin: '0 4px' }}>已核实字段</Tag>
            的图表解析自左侧清单中的已核实取值，与证据附录同源；
            带<Tag color="orange" style={{ margin: '0 4px' }}>探索性</Tag>
            的内容来自尚未通过证据闸门的调查材料，<b>不得作为授信依据</b>。
          </Text>
        }
      />

      {deterministic.map(c => <ChartBlock key={c.id} chart={c} />)}

      {(exploratory.length > 0 || data.findings.length > 0
        || (data.graph?.edges?.length ?? 0) > 0) && (
        <>
          <Paragraph style={{ margin: '10px 0 6px', fontSize: 12 }}>
            <Text strong>探索性调查发现</Text>
            <Text type="secondary">（未经核实，仅供人工参考）</Text>
          </Paragraph>
          {exploratory.map(c => <ChartBlock key={c.id} chart={c} />)}
          <RelationGraph graph={data.graph} />
          <FindingList findings={data.findings} />
        </>
      )}

      {!hasAnything && (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={<Text type="secondary" style={{ fontSize: 12 }}>本次调查层未产出内容</Text>} />
      )}

      {/* 少一节的原因必须写明，且区分「查了没有」与「没查成」——
          不留痕的话，读者会以为这一节本来就不存在 */}
      {data.failures.length > 0 && (
        <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px dashed #e8e8e8' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>本层未能产出的部分：</Text>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
            {data.failures.map((f, i) => {
              const meta = FAILURE_META[f.kind] || FAILURE_META.error
              return (
                <li key={i}>
                  <Tag color={meta.color} style={{ marginInlineEnd: 4 }}>{meta.label}</Tag>
                  <Text style={{ fontSize: 12 }}>{f.stage}：{f.reason}</Text>
                  {f.detail && (
                    <Text type="secondary" style={{ fontSize: 11 }}>{'　'}{f.detail}</Text>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </Card>
  )
}
