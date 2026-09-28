export type InvestigationStatus = 'not_started' | 'running' | 'completed' | 'stalled' | 'time_limit' | 'step_limit' | 'cancelled'

export interface FindingCitation {
  source_id: string
  quote_id: string
  quote: string
  title: string
  url: string
}

export interface MaterialDocument {
  title: string
  source_id: string
  chunk_count: number
  read_chunks: number
  index_status: 'pending' | 'processing' | 'completed' | 'failed' | 'unknown'
}

export interface MaterialCoverage {
  catalog_status: 'available' | 'unavailable' | 'not_requested'
  documents_total: number
  documents_read: number
  known_chunks: number
  read_chunks: number
  truncated: boolean
  documents: MaterialDocument[]
}

export interface CalculationVariable extends FindingCitation {
  value: string
  unit: string
  period: string
  subject: string
}

export interface CalculationWorkpaper {
  id: string
  label: string
  expression: string
  variables: Record<string, CalculationVariable>
  result: string
  result_unit: string
  limitations: string
  literal_constants: string[]
  arithmetic_status: 'computed'
  inference_status: 'not_reviewed'
  verified: false
}

export interface InvestigationQuestion {
  id: string
  question: string
  done_when: string
  calculation_required: boolean
  status: 'open' | 'answered' | 'blocked'
  answer: string
  citations: FindingCitation[]
  calculation_ids: string[]
  limitations: string
}

export interface AgentNotebook {
  status: InvestigationStatus
  questions: string[]
  findings: {
    claim: string
    quote: string
    title: string
    source_id: string
    kind: string
    citations: FindingCitation[]
    calculation_ids: string[]
  }[]
  summary: string
  missing_materials: string[]
  material_coverage: MaterialCoverage | null
  calculations: CalculationWorkpaper[]
  investigation_plan: InvestigationQuestion[]
  display_warnings: string[]
}

type RecordValue = Record<string, unknown>
const record = (value: unknown): value is RecordValue => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
const text = (value: unknown, max: number, nonempty = false): value is string =>
  typeof value === 'string' && value.length <= max && (!nonempty || Boolean(value.trim()))
const count = (value: unknown): value is number => Number.isSafeInteger(value) && typeof value === 'number' && value >= 0 && value <= 1_000_000
const id = (value: unknown): value is string => text(value, 128, true)
const has = (value: RecordValue, key: string) => Object.prototype.hasOwnProperty.call(value, key)

function strings(value: unknown, maximum: number, length: number): string[] | null {
  if (!Array.isArray(value) || value.length > maximum || !value.every(item => text(item, length, true))) return null
  return [...value]
}

// Source URLs are display metadata only, never rendered as executable links.
function sourceUrl(value: unknown): string {
  return text(value, 2048) && /^(?:https?:\/\/|local:\/\/kb\/)/i.test(value) ? value : ''
}

function citation(value: unknown, legacy = false, quoteLimit = 1200): FindingCitation | null {
  if (!record(value) || !id(value.source_id) || !text(value.quote, quoteLimit, true)) return null
  if (!legacy && !id(value.quote_id)) return null
  if (value.title !== undefined && !text(value.title, 500)) return null
  return {
    source_id: value.source_id, quote_id: id(value.quote_id) ? value.quote_id : '',
    quote: value.quote, title: typeof value.title === 'string' ? value.title : '', url: sourceUrl(value.url),
  }
}

function coverage(value: unknown): MaterialCoverage | null {
  if (!record(value) || typeof value.catalog_status !== 'string' || !['available', 'unavailable', 'not_requested'].includes(value.catalog_status)) return null
  if (![value.documents_total, value.documents_read, value.known_chunks, value.read_chunks].every(count) || typeof value.truncated !== 'boolean') return null
  if (!Array.isArray(value.documents) || value.documents.length > 100) return null
  const documents: MaterialDocument[] = []
  const seen = new Set<string>()
  for (const item of value.documents) {
    if (!record(item) || !text(item.title, 500, true) || !id(item.source_id) || seen.has(item.source_id)) return null
    if (!count(item.chunk_count) || !count(item.read_chunks) || item.read_chunks > item.chunk_count) return null
    if (typeof item.index_status !== 'string' || !['pending', 'processing', 'completed', 'failed', 'unknown'].includes(item.index_status)) return null
    seen.add(item.source_id)
    documents.push({ title: item.title, source_id: item.source_id, chunk_count: item.chunk_count,
      read_chunks: item.read_chunks, index_status: item.index_status as MaterialDocument['index_status'] })
  }
  // All counts are validated above; do not coerce a string count into an apparently valid receipt.
  const documentsTotal = value.documents_total as number
  const documentsRead = value.documents_read as number
  const knownChunks = value.known_chunks as number
  const readChunks = value.read_chunks as number
  if (documentsRead > documentsTotal || readChunks > knownChunks || documents.length > documentsTotal) return null
  return { catalog_status: value.catalog_status as MaterialCoverage['catalog_status'],
    documents_total: documentsTotal, documents_read: documentsRead, known_chunks: knownChunks,
    read_chunks: readChunks, truncated: value.truncated, documents }
}

const decimal = (value: unknown): value is string => text(value, 128, true) && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(value)

function calculation(value: unknown): CalculationWorkpaper | null {
  if (!record(value) || !id(value.id) || !text(value.label, 200, true) || !text(value.expression, 500, true)) return null
  if (!decimal(value.result) || !text(value.result_unit, 80) || !text(value.limitations, 1800)) return null
  if (value.arithmetic_status !== 'computed' || value.inference_status !== 'not_reviewed' || value.verified !== false) return null
  if (!record(value.variables)) return null
  const names = Object.keys(value.variables)
  if (!names.length || names.length > 20) return null
  const variables: Record<string, CalculationVariable> = {}
  for (const name of names) {
    if (!/^[A-Za-z][A-Za-z0-9_]{0,31}$/.test(name) || ['constructor', 'prototype', '__proto__'].includes(name)) return null
    const item = value.variables[name]
    const origin = citation(item, false, 5000)
    if (!record(item) || !origin || !decimal(item.value) || !text(item.unit, 80) || !text(item.period, 120)) return null
    if (item.subject !== undefined && !text(item.subject, 200)) return null
    variables[name] = { ...origin, value: item.value, unit: item.unit, period: item.period,
      subject: typeof item.subject === 'string' ? item.subject : '' }
  }
  const constants = value.literal_constants === undefined ? [] : strings(value.literal_constants, 120, 3)
  if (!constants || constants.some(item => !['0', '1', '100'].includes(item))) return null
  return { id: value.id, label: value.label, expression: value.expression, variables,
    result: value.result, result_unit: value.result_unit, limitations: value.limitations,
    literal_constants: constants, arithmetic_status: 'computed', inference_status: 'not_reviewed', verified: false }
}

function investigationQuestion(value: unknown, calculationIds: Set<string>): InvestigationQuestion | null {
  if (!record(value) || !id(value.id) || !text(value.question, 300, true) || !text(value.done_when, 400, true)) return null
  if (typeof value.calculation_required !== 'boolean' || typeof value.status !== 'string' || !['open', 'answered', 'blocked'].includes(value.status)) return null
  if (!text(value.answer, 1200) || !text(value.limitations, 600) || !Array.isArray(value.citations) || value.citations.length > 6) return null
  const citations: FindingCitation[] = []
  const identities = new Set<string>()
  for (const raw of value.citations) {
    const origin = citation(raw)
    if (!origin) return null
    const identity = `${origin.source_id}\u0000${origin.quote_id}`
    if (identities.has(identity)) return null
    identities.add(identity)
    citations.push(origin)
  }
  const refs = strings(value.calculation_ids, 6, 128)
  if (!refs || new Set(refs).size !== refs.length || refs.some(ref => !calculationIds.has(ref))) return null
  if (value.status === 'answered' && (!value.answer.trim() || !citations.length || (value.calculation_required && !refs.length))) return null
  if (value.status === 'blocked' && !value.limitations.trim()) return null
  return { id: value.id, question: value.question, done_when: value.done_when,
    calculation_required: value.calculation_required, status: value.status as InvestigationQuestion['status'],
    answer: value.answer, citations, calculation_ids: refs, limitations: value.limitations }
}

export function parseAgentNotebook(value: unknown): AgentNotebook | null {
  if (!record(value)) return null
  const materialCoverage = has(value, 'material_coverage') ? coverage(value.material_coverage) : null
  // The server publishes the authorized directory before the investigation loop
  // starts. Only this explicit null + valid available directory means not_started.
  const status = value.status === null && materialCoverage?.catalog_status === 'available' ? 'not_started' : value.status
  if (typeof status !== 'string' || !['not_started', 'running', 'completed', 'stalled', 'time_limit', 'step_limit', 'cancelled'].includes(status)) return null
  const warnings = new Set<string>()
  const boundedStrings = (items: unknown) => {
    if (items === undefined || items === null) return []
    const parsed = strings(items, 8, 400)
    if (!parsed) warnings.add('部分问题或补件回执格式无效，未展示；不能视为已解决。')
    return parsed || []
  }
  const findings: AgentNotebook['findings'] = []
  if (value.findings !== undefined && value.findings !== null && (!Array.isArray(value.findings) || value.findings.length > 20)) {
    warnings.add('调查发现回执超出显示范围或格式无效，未完整展示。')
  }
  for (const raw of Array.isArray(value.findings) ? value.findings.slice(0, 20) : []) {
    const fail = () => warnings.add('部分调查发现的引文或类型无效，未展示。')
    if (!record(raw) || !text(raw.claim, 700, true) || (raw.kind !== undefined && (typeof raw.kind !== 'string' || !['support', 'counter', 'gap', ''].includes(raw.kind)))) {
      fail()
      continue
    }
    const origins: FindingCitation[] = []
    if (has(raw, 'citations')) {
      if (!Array.isArray(raw.citations) || !raw.citations.length || raw.citations.length > 8) {
        fail()
        continue
      }
      for (const item of raw.citations) {
        const parsed = citation(item)
        if (parsed) origins.push(parsed)
      }
      const identities = new Set(origins.map(origin => `${origin.source_id}\u0000${origin.quote_id}`))
      if (origins.length !== raw.citations.length || identities.size !== origins.length) { fail(); continue }
    } else {
      const original = citation(raw, true)
      if (!original) { fail(); continue }
      origins.push(original)
    }
    const calculationIds = raw.calculation_ids === undefined ? [] : strings(raw.calculation_ids, 20, 128)
    if (!calculationIds) { fail(); continue }
    findings.push({ claim: raw.claim, quote: origins[0].quote, title: origins[0].title,
      source_id: origins[0].source_id, kind: typeof raw.kind === 'string' ? raw.kind : '',
      citations: origins, calculation_ids: calculationIds })
  }
  if (has(value, 'material_coverage') && !materialCoverage) warnings.add('材料目录回执格式无效，无法确认阅读覆盖范围。')
  const calculations: CalculationWorkpaper[] = []
  let calculationChars = 0
  if (value.calculations !== undefined && value.calculations !== null && (!Array.isArray(value.calculations) || value.calculations.length > 12)) {
    warnings.add('计算底稿回执超出显示范围或格式无效，未完整展示。')
  }
  const seenCalculations = new Set<string>()
  for (const raw of Array.isArray(value.calculations) ? value.calculations.slice(0, 12) : []) {
    const parsed = calculation(raw)
    const size = parsed ? JSON.stringify(parsed).length : 0
    if (!parsed || seenCalculations.has(parsed.id) || calculationChars + size > 100_000) {
      warnings.add('部分计算底稿格式、状态或显示预算无效，未展示；不能视为已核实。')
      continue
    }
    seenCalculations.add(parsed.id)
    calculationChars += size
    calculations.push(parsed)
  }
  const plan: InvestigationQuestion[] = []
  if (value.investigation_plan !== undefined && value.investigation_plan !== null) {
    const rows = value.investigation_plan
    const invalidPlan = () => warnings.add('部分调查问题的状态或证据回执无效，未展示；不能据此确认问题已全部回答。')
    // Do not show a truncated prefix as a complete investigation plan.
    if (!Array.isArray(rows) || rows.length > 6) invalidPlan()
    else {
      const occurrences = new Map<unknown, number>()
      for (const raw of rows) if (record(raw)) occurrences.set(raw.id, (occurrences.get(raw.id) || 0) + 1)
      for (const raw of rows) {
        const parsed = investigationQuestion(raw, seenCalculations)
        // Conflicting duplicates cannot prefer an earlier "answered" receipt.
        if (!parsed || occurrences.get(parsed.id) !== 1) { invalidPlan(); continue }
        plan.push(parsed)
      }
    }
  }
  const summary = text(value.summary, 2000) ? value.summary : ''
  if (value.summary !== undefined && value.summary !== null && !text(value.summary, 2000)) warnings.add('调查概述格式无效，未展示。')
  const questions = boundedStrings(value.questions)
  const missing = boundedStrings(value.missing_materials)
  return { status: status as InvestigationStatus, questions, findings, summary, missing_materials: missing,
    material_coverage: materialCoverage, calculations, investigation_plan: plan, display_warnings: [...warnings] }
}
