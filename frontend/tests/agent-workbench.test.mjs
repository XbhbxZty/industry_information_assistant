// node --test tests/agent-workbench.test.mjs
// Component semantics use real React escaping with expanded presentation stubs;
// these tests do not claim to cover Ant Design's browser interaction behavior.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import { runInNewContext } from 'node:vm'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'

const require = createRequire(import.meta.url)
function load(file, resolver = require) {
  const source = readFileSync(new URL(`../src/pages/due-diligence/${file}`, import.meta.url), 'utf8')
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText
  const module = { exports: {} }
  runInNewContext(compiled, { module, exports: module.exports, require: resolver, AbortController, TextDecoder, Date })
  return module.exports
}

const notebookModule = load('agent-notebook.ts')
const { parseAgentNotebook } = notebookModule
const element = (tag, children, props = {}) => createElement(tag, props, children)
const primitives = {
  Card: ({ title, children }) => element('section', [element('h2', title, { key: 'title' }), children]),
  Space: ({ children }) => element('div', children),
  Tag: ({ children }) => element('span', children),
  Typography: { Text: ({ children }) => element('span', children) },
  Alert: ({ message, description }) => element('aside', [element('div', message, { key: 'message' }), description]),
  Collapse: ({ items }) => element('div', items.map(item => element('details', [
    element('summary', item.label, { key: 'label' }), element('div', item.children, { key: 'content' }),
  ], { key: item.key }))),
}
const { AgentWorkbench } = load('agent-workbench.tsx', name => name === 'antd' ? primitives : require(name))
const render = notebook => renderToStaticMarkup(createElement(AgentWorkbench, { notebook }))
const plain = value => JSON.parse(JSON.stringify(value))
const clone = value => JSON.parse(JSON.stringify(value))

const origin = (patch = {}) => ({
  source_id: 's1', quote_id: 'q1', quote: '本期应收款合计 435 万元，期后回款 300 万元。',
  title: '回款材料', url: 'local://kb/kb1/doc1', ...patch,
})
const calculation = (patch = {}) => ({
  id: 'calc1', label: '同一债权池回款覆盖率', expression: 'received / balance * 100',
  variables: {
    received: { ...origin(), value: '300', unit: '万元', period: '2026年一季度', subject: '测试主体' },
    balance: { ...origin(), value: '435', unit: '万元', period: '2025年末', subject: '测试主体' },
  },
  result: '68.965517241379310344827586206896551724137931034483', result_unit: '%',
  literal_constants: ['100'], limitations: '须复核回款是否对应同一债权池；不能据此证明资金用途。',
  arithmetic_status: 'computed', inference_status: 'not_reviewed', verified: false, ...patch,
})
const coverage = (patch = {}) => ({
  catalog_status: 'available', documents_total: 4, documents_read: 1, known_chunks: 8,
  read_chunks: 1, truncated: false, documents: [
    { title: '回款材料', source_id: 's1', chunk_count: 4, read_chunks: 1, index_status: 'completed' },
    { title: '合同材料', source_id: 's2', chunk_count: 4, read_chunks: 0, index_status: 'completed' },
    { title: '待处理报表', source_id: 's3', chunk_count: 0, read_chunks: 0, index_status: 'processing' },
    { title: '处理失败材料', source_id: 's4', chunk_count: 0, read_chunks: 0, index_status: 'failed' },
  ], ...patch,
})
const sample = (patch = {}) => ({
  status: 'running', questions: ['回款归属是否一致？'], missing_materials: [], summary: '目前仅支持部分分析。',
  findings: [{ claim: '回款可以覆盖部分期末余额，仍须核对同一债权池。', kind: 'support',
    citations: [origin(), origin({ source_id: 's2', quote_id: 'q2', quote: '对应债权池需由核销记录进一步确认。' })],
    calculation_ids: ['calc1'] }],
  material_coverage: coverage(), calculations: [calculation()], ...patch,
})

function streamHarness(events) {
  let current
  const hook = load('useDDStream.ts', name => {
    if (name === 'react') return {
      useState: initial => { current = initial; return [current, update => { current = typeof update === 'function' ? update(current) : update }] },
      useRef: initial => ({ current: initial }), useCallback: callback => callback,
    }
    if (name === './agent-notebook') return notebookModule
    if (name === './outcome') return load('outcome.ts')
    if (name === '@/api/duediligence') return {
      startDueDiligence: async () => ({ data: new Response(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join('')).body }),
      submitReview: async () => { throw new Error('Must not approve a loan to display workpapers') },
    }
    throw new Error(`Unexpected dependency ${name}`)
  }).useDDStream()
  return { hook, state: () => current }
}

test('new receipts are bounded projections, preserving multiple exact quotations and calculation precision', () => {
  const input = sample({ sources: { secret: 'raw' }, actions: ['private-action'], token: 'private-token' })
  input.calculations[0].secret = 'internal'
  input.material_coverage.documents[0].file_path = 'C:/private'
  const parsed = parseAgentNotebook(input)
  assert.equal(parsed.findings[0].citations.length, 2)
  assert.equal(parsed.findings[0].quote, input.findings[0].citations[0].quote)
  assert.equal(parsed.calculations[0].result, input.calculations[0].result)
  assert.equal(parsed.calculations[0].verified, false)
  assert.equal(parsed.material_coverage.documents[0].read_chunks, 1)
  assert.deepEqual(plain(parsed.display_warnings), [])
  assert.equal(JSON.stringify(parsed).includes('private'), false)
  assert.equal(JSON.stringify(parsed).includes('internal'), false)
  input.calculations[0].variables.received.quote = 'changed'
  input.findings[0].citations[0].quote = 'changed'
  assert.notEqual(parsed.calculations[0].variables.received.quote, 'changed')
  assert.notEqual(parsed.findings[0].citations[0].quote, 'changed')
})

test('legacy notebooks retain old findings and never invent coverage or computation', () => {
  const legacy = { status: 'stalled', findings: [{ claim: '这项回款需要进一步调查。', quote: '应收账款尚需核对。', source_id: 's1', title: '旧材料', kind: 'gap' }],
    summary: '旧概述', missing_materials: ['需要核销对应关系'] }
  const parsed = parseAgentNotebook(legacy)
  assert.equal(parsed.findings[0].citations[0].quote_id, '')
  assert.equal(parsed.findings[0].quote, legacy.findings[0].quote)
  assert.equal(parsed.material_coverage, null)
  assert.deepEqual(plain(parsed.calculations), [])
  assert.deepEqual(plain(parsed.display_warnings), [])
  const html = render(parsed)
  assert.match(html, /无法确认阅读覆盖范围/)
  assert.match(html, /尚无可展示的计算回执/)
  assert.match(html, /旧概述/)
  assert.match(html, /需要核销对应关系/)
})

test('unknown top-level statuses and coerced arrays are rejected', () => {
  for (const value of [null, [], 2, 'complete', { status: 'approved' }, { status: ['completed'] }, { status: true }]) {
    assert.equal(parseAgentNotebook(value), null)
  }
})

test('unknown material states and malformed counters never imply completed reading', () => {
  for (const patch of [
    { catalog_status: 'verified' }, { catalog_status: ['available'] }, { documents_total: '4' },
    { documents_read: 5 }, { read_chunks: 9 }, { known_chunks: -1 }, { truncated: 'false' },
    { documents_total: Number.MAX_SAFE_INTEGER + 1 }, { known_chunks: Infinity },
    { documents: [{ ...coverage().documents[0], index_status: 'ready' }] },
    { documents: [{ ...coverage().documents[0], index_status: ['completed'] }] },
    { documents: [{ ...coverage().documents[0], read_chunks: 5 }] },
    { documents: [coverage().documents[0], coverage().documents[0]] },
  ]) {
    const parsed = parseAgentNotebook(sample({ material_coverage: coverage(patch) }))
    assert.equal(parsed.material_coverage, null, JSON.stringify(patch))
    assert.match(parsed.display_warnings.join(''), /材料目录回执格式无效/)
    assert.equal(parsed.findings.length, 1)
  }
})

test('calculation status and operands are not coerced, promoted to verified, or accepted with arbitrary keys', () => {
  const badVariables = clone(calculation().variables)
  badVariables.received.value = 300
  const polluted = JSON.parse('{"__proto__":{"value":"1","source_id":"s1","quote_id":"q1","quote":"1万元","unit":"万元","period":"2025"}}')
  for (const patch of [
    { arithmetic_status: 'approved' }, { inference_status: 'verified' }, { verified: true }, { verified: 'false' },
    { result: 'NaN' }, { result: 'Infinity' }, { result: 0 }, { result: '1e100000' },
    { variables: badVariables }, { variables: polluted }, { variables: [] },
    { literal_constants: ['800'] }, { literal_constants: [100] }, { expression: 'x'.repeat(501) },
    { variables: { a: { ...calculation().variables.received, quote_id: 1 } } },
  ]) {
    const parsed = parseAgentNotebook(sample({ calculations: [calculation(patch)] }))
    assert.equal(parsed.calculations.length, 0, JSON.stringify(patch))
    assert.match(parsed.display_warnings.join(''), /不能视为已核实/)
  }
  assert.equal({}.polluted, undefined)
})

test('multi-citation contract rejects malformed members instead of silently falling back to one quote', () => {
  for (const patch of [
    { citations: 'q1,q2' }, { citations: [origin(), { ...origin(), quote_id: [] }] },
    { citations: [origin(), origin()] }, { citations: [] }, { kind: 'verified' }, { kind: ['support'] },
    { calculation_ids: [true] }, { claim: 'a'.repeat(701) },
  ]) {
    const input = sample()
    input.findings[0] = { ...input.findings[0], ...origin(), ...patch }
    const parsed = parseAgentNotebook(input)
    assert.equal(parsed.findings.length, 0, JSON.stringify(patch))
    assert.ok(parsed.display_warnings.length)
  }
})

test('oversized collections and text budgets are visibly bounded', () => {
  const parsed = parseAgentNotebook(sample({
    questions: Array(9).fill('问题'), summary: '长'.repeat(2001),
    findings: Array(21).fill(sample().findings[0]),
    calculations: Array.from({ length: 13 }, (_, index) => calculation({ id: `calc${index}` })),
  }))
  assert.equal(parsed.findings.length, 20)
  assert.equal(parsed.calculations.length, 12)
  assert.equal(parsed.summary, '')
  assert.equal(parsed.questions.length, 0)
  assert.ok(parsed.display_warnings.length >= 4)
  const variables = Object.fromEntries(Array.from({ length: 20 }, (_, index) => [`v${index}`, {
    ...calculation().variables.received, quote: '原文'.repeat(2400),
  }]))
  const oversized = parseAgentNotebook(sample({ calculations: [calculation({ variables }), calculation({ id: 'calc2', variables })] }))
  assert.ok(oversized.calculations.length <= 1)
  assert.match(oversized.display_warnings.join(''), /显示预算/)
})

test('markup remains escaped text, unsafe URLs disappear and repeated calculation IDs are not duplicated', () => {
  const input = sample()
  input.findings[0].claim = '<script>window.injected=true</script>'
  input.findings[0].citations[0].quote = '<img src=x onerror=alert(1)>'
  input.findings[0].citations[0].url = 'javascript:alert(1)'
  input.calculations.push(clone(input.calculations[0]))
  const parsed = parseAgentNotebook(input)
  assert.equal(parsed.calculations.length, 1)
  assert.equal(parsed.findings[0].citations[0].url, '')
  const html = render(parsed)
  assert.match(html, /&lt;script&gt;/)
  assert.match(html, /&lt;img/)
  assert.doesNotMatch(html, /<script|<img|href="javascript/)
})

test('workbench distinguishes unread, partial, processing and failed materials without implying full coverage', () => {
  const html = render(parseAgentNotebook(sample({ material_coverage: coverage({ truncated: true }) })))
  assert.match(html, /1 \/ 4 份（至少一片）/)
  assert.match(html, /已读部分片段/)
  assert.match(html, /尚未阅读/)
  assert.match(html, /处理中，材料已登记/)
  assert.match(html, /处理失败，不能视为未提供/)
  assert.match(html, /一份材料读过一片，不代表全文已读/)
  assert.match(html, /统计已截断/)
})

test('unavailable and unrequested catalogs cannot be presented as no supplied materials', () => {
  for (const catalog_status of ['unavailable', 'not_requested']) {
    const html = render(parseAgentNotebook(sample({ material_coverage: coverage({ catalog_status, documents: [] }) })))
    assert.match(html, /不能据此判定材料未提供/)
    assert.doesNotMatch(html, /目录中暂无材料/)
  }
})

test('formula, inputs, citations, units, periods, limitations and missing calculation receipts stay visible', () => {
  const input = sample()
  input.findings[0].calculation_ids.push('missing-calculation')
  const html = render(parseAgentNotebook(input))
  for (const expected of ['received / balance * 100', calculation().result, '300', '435', '万元', '2025年末', '2026年一季度',
    's1 / q1', '测试主体', '不能据此证明资金用途', '查看 2 处原文依据', 'missing-calculation（回执未展示）']) {
    assert.ok(html.includes(expected), expected)
  }
  assert.match(html, /计算正确不代表来源真实、口径可比或因果解释正确/)
  assert.match(html, /原文未独立核实/)
  assert.match(html, /分析结论待复核/)
  assert.equal(render(null), '')
})

test('actual SSE hook carries tool receipts to the workbench and never upgrades the risk assessment', async () => {
  const risk = { level: '证据不足', requires_human_review: true }
  const finalNotebook = sample({ status: 'completed', summary: '本轮形成有引用的有限分析，未替代字段核实。' })
  const run = streamHarness([
    { type: 'agent_investigation', content: sample() },
    { type: 'research_complete', agent_investigation: finalNotebook, risk_assessment: risk, final_report: '有限报告' },
  ])
  await run.hook.start('分析回款')
  assert.deepEqual(plain(run.state().risk), risk)
  assert.deepEqual(plain(run.state().agentNotebook), plain(parseAgentNotebook(finalNotebook)))
  assert.match(render(run.state().agentNotebook), /计算底稿：1 项/)
  run.hook.reset()
  assert.equal(run.state().agentNotebook, null)
})

test('an invalid later event cannot replace a valid workbench with fabricated success', async () => {
  const run = streamHarness([
    { type: 'agent_investigation', content: sample() },
    { type: 'agent_investigation', content: { status: 'approved', calculations: [calculation({ verified: true })] } },
    { type: 'research_complete', agent_investigation: { status: ['completed'] } },
  ])
  await run.hook.start('分析回款')
  assert.equal(run.state().agentNotebook.status, 'running')
  assert.equal(run.state().agentNotebook.calculations[0].verified, false)
})
