export interface AgentNotebook {
  status: string
  questions: string[]
  findings: { claim: string; quote: string; title: string; source_id: string; kind: string }[]
  summary: string
  missing_materials: string[]
}

export function parseAgentNotebook(value: unknown): AgentNotebook | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const obj = value as Record<string, unknown>
  if (typeof obj.status !== 'string') return null
  const strings = (xs: unknown) => Array.isArray(xs) ? xs.filter((x): x is string => typeof x === 'string').slice(0, 8) : []
  const findings: AgentNotebook['findings'] = []
  for (const raw of Array.isArray(obj.findings) ? obj.findings.slice(0, 20) : []) {
    if (!raw || typeof raw !== 'object') continue
    const f = raw as Record<string, unknown>
    if (typeof f.claim !== 'string' || typeof f.quote !== 'string' || typeof f.source_id !== 'string') continue
    findings.push({ claim: f.claim, quote: f.quote, source_id: f.source_id,
      title: typeof f.title === 'string' ? f.title : '', kind: typeof f.kind === 'string' ? f.kind : '' })
  }
  return { status: obj.status, questions: strings(obj.questions), findings,
    summary: typeof obj.summary === 'string' ? obj.summary : '', missing_materials: strings(obj.missing_materials) }
}
