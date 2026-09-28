// node --test tests/research-outcome.test.mjs
// Uses the existing TypeScript compiler and Node runner; no additional framework.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

function load(file, require = () => { throw new Error('Unexpected dependency') }) {
  const source = readFileSync(new URL(`../src/pages/due-diligence/${file}`, import.meta.url), 'utf8')
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  const module = { exports: {} }
  runInNewContext(compiled, {
    module, exports: module.exports, require, AbortController, TextDecoder, Date,
  })
  return module.exports
}

const outcome = load('outcome.ts')
const notebook = load('agent-notebook.ts')
const plain = value => JSON.parse(JSON.stringify(value))
const sample = (overrides = {}) => ({
  version: 1, execution_status: 'finished', investigation_status: 'stalled',
  quality_status: 'needs_revision', report_status: 'restricted',
  rating_status: 'available', credit_status: 'available', requires_human_review: false,
  outstanding_issue_count: 1, restriction_reasons: ['调查未完成；历史质检问题尚未解决'],
  ...overrides,
})

function streamHarness(events) {
  let current
  const hook = load('useDDStream.ts', name => {
    if (name === 'react') return {
      useState: initial => {
        current = initial
        return [current, update => { current = typeof update === 'function' ? update(current) : update }]
      },
      useRef: initial => ({ current: initial }),
      useCallback: callback => callback,
    }
    if (name === './outcome') return outcome
    if (name === './agent-notebook') return notebook
    if (name === '@/api/duediligence') return {
      startDueDiligence: async () => ({
        data: new Response(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join('')).body,
      }),
      submitReview: async () => { throw new Error('Review protocol must not be invoked') },
    }
    throw new Error(`Unexpected dependency ${name}`)
  }).useDDStream()
  return { hook, state: () => current }
}

test('version 1 is projected strictly and does not retain arbitrary fields', () => {
  const input = sample({ private_token: 'not-public' })
  const parsed = outcome.parseResearchOutcome(input)
  assert.deepEqual(plain(parsed), sample())
  input.restriction_reasons.push('later mutation')
  assert.equal(parsed.restriction_reasons.length, 1)
})

test('missing fields, unknown versions/enums and malformed scalars are rejected', () => {
  for (const key of Object.keys(sample())) {
    const incomplete = sample()
    delete incomplete[key]
    assert.equal(outcome.parseResearchOutcome(incomplete), null, `missing ${key}`)
  }
  for (const patch of [
    { version: 2 }, { version: '1' }, { execution_status: 'completed' },
    { investigation_status: 'passed' }, { quality_status: 'approved' },
    { report_status: 'success' }, { rating_status: 'low_risk' }, { credit_status: 'approved' },
    { requires_human_review: 'false' }, { outstanding_issue_count: -1 },
    { outstanding_issue_count: 0.5 }, { outstanding_issue_count: Number.MAX_SAFE_INTEGER + 1 },
    { restriction_reasons: [42] }, { restriction_reasons: [' '] }, { restriction_reasons: 'reason' },
  ]) assert.equal(outcome.parseResearchOutcome(sample(patch)), null, JSON.stringify(patch))
  for (const input of [null, undefined, [], 0, 'completed']) {
    assert.equal(outcome.parseResearchOutcome(input), null)
  }
})

test('nested writer and root terminal payloads share the same parser', () => {
  const payload = sample()
  assert.deepEqual(plain(outcome.extractResearchOutcome({ content: { research_outcome: payload } })), payload)
  assert.deepEqual(plain(outcome.extractResearchOutcome({ research_outcome: payload })), payload)
  assert.equal(outcome.extractResearchOutcome({ type: 'research_complete' }), null)
})

test('missing status does not claim quality passed, and restricted/partial retain explicit labels', () => {
  const unknown = outcome.describeResearchOutcome(null)
  assert.equal(unknown.title, '调查与质检状态尚未确认')
  assert.deepEqual(plain(unknown.dimensions), [])
  const restricted = outcome.describeResearchOutcome(sample())
  assert.equal(restricted.title, '受限草稿')
  assert.match(restricted.explanation, /执行结束不代表调查\/质检通过/)
  assert.ok(restricted.dimensions.includes('评级：已生成规则评级'))
  assert.ok(restricted.dimensions.includes('质检：待修订 / 复核'))
  assert.equal(outcome.describeResearchOutcome(sample({ report_status: 'partial' })).title, '部分材料报告')
  const degraded = outcome.parseResearchOutcome(sample({ execution_status: 'degraded' }))
  assert.ok(degraded)
  assert.ok(outcome.describeResearchOutcome(degraded).dimensions.includes('执行：执行存在异常'))
})

test('restricted completion preserves report and rating without turning execution into an error', async () => {
  const risk = { level: '低风险', composite_score: 4, requires_human_review: false }
  const testRun = streamHarness([
    { type: 'report_draft', content: { content: '已有草稿', research_outcome: sample({ execution_status: 'running' }) } },
    { type: 'research_complete', final_report: '受限终稿', risk_assessment: risk, research_outcome: sample() },
  ])
  await testRun.hook.start('调查问题')
  assert.equal(testRun.state().phase, 'completed')
  assert.equal(testRun.state().report, '受限终稿')
  assert.deepEqual(plain(testRun.state().risk), risk)
  assert.equal(testRun.state().researchOutcome.report_status, 'restricted')
  assert.equal(testRun.state().reviewRequest, null)
})

test('nested draft outcome survives missing or malformed later events', async () => {
  for (const invalid of [undefined, { version: 2 }, { version: 1, report_status: 'ready' }]) {
    const testRun = streamHarness([
      { type: 'report_draft', content: { content: '草稿', research_outcome: sample() } },
      { type: 'research_complete', research_outcome: invalid },
    ])
    await testRun.hook.start('调查问题')
    assert.equal(testRun.state().report, '草稿')
    assert.deepEqual(plain(testRun.state().researchOutcome), sample())
  }
})

test('human review keeps its existing pause protocol and separately parses outcome', async () => {
  const pending = sample({ execution_status: 'awaiting_review', requires_human_review: true })
  const testRun = streamHarness([
    { type: 'report_draft', content: { content: '复核草稿' } },
    { type: 'human_review_required', session_id: 's1', level: '高风险', research_outcome: pending },
  ])
  await testRun.hook.start('调查问题')
  assert.equal(testRun.state().phase, 'awaiting_review')
  assert.equal(testRun.state().report, '复核草稿')
  assert.equal(testRun.state().reviewRequest.level, '高风险')
  assert.equal(testRun.state().reviewRequest.research_outcome, undefined)
  assert.deepEqual(plain(testRun.state().researchOutcome), pending)
})

test('legacy completion never fabricates outcome and reset clears earlier state', async () => {
  const legacy = streamHarness([{ type: 'research_complete', final_report: '旧报告' }])
  await legacy.hook.start('调查问题')
  assert.equal(legacy.state().phase, 'completed')
  assert.equal(legacy.state().researchOutcome, null)
  const testRun = streamHarness([{ type: 'research_complete', research_outcome: sample() }])
  await testRun.hook.start('调查问题')
  assert.ok(testRun.state().researchOutcome)
  testRun.hook.reset()
  assert.equal(testRun.state().researchOutcome, null)
  assert.equal(testRun.state().phase, 'idle')
})
