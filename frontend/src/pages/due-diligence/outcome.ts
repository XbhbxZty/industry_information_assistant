// 执行终态、调查质量与规则评级是独立维度；不得由 completed 推断质检通过。
const EXECUTION = ['running', 'finished', 'awaiting_review', 'cancelled', 'degraded'] as const
const INVESTIGATION = ['not_started', 'not_applicable', 'running', 'completed', 'stalled', 'time_limit', 'step_limit', 'unknown'] as const
const QUALITY = ['passed', 'needs_revision', 'not_reviewed'] as const
const REPORT = ['unavailable', 'restricted', 'partial', 'draft', 'ready'] as const
const RATING = ['not_evaluated', 'insufficient', 'available'] as const
const CREDIT = ['available', 'unavailable'] as const

export interface ResearchOutcome {
  version: 1
  execution_status: typeof EXECUTION[number]
  investigation_status: typeof INVESTIGATION[number]
  quality_status: typeof QUALITY[number]
  report_status: typeof REPORT[number]
  rating_status: typeof RATING[number]
  credit_status: typeof CREDIT[number]
  requires_human_review: boolean
  outstanding_issue_count: number
  restriction_reasons: string[]
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null
}

function member<T extends string>(value: unknown, choices: readonly T[]): value is T {
  return typeof value === 'string' && choices.includes(value as T)
}

/** 不接受半截状态或未知协议版本；保留原状态比推断“通过”更安全。 */
export function parseResearchOutcome(value: unknown): ResearchOutcome | null {
  const obj = record(value)
  if (!obj || obj.version !== 1
    || !member(obj.execution_status, EXECUTION)
    || !member(obj.investigation_status, INVESTIGATION)
    || !member(obj.quality_status, QUALITY)
    || !member(obj.report_status, REPORT)
    || !member(obj.rating_status, RATING)
    || !member(obj.credit_status, CREDIT)
    || typeof obj.requires_human_review !== 'boolean'
    || typeof obj.outstanding_issue_count !== 'number'
    || !Number.isSafeInteger(obj.outstanding_issue_count)
    || obj.outstanding_issue_count < 0
    || !Array.isArray(obj.restriction_reasons)
    || !obj.restriction_reasons.every((reason): reason is string => typeof reason === 'string' && Boolean(reason.trim()))
  ) return null
  return {
    version: 1,
    execution_status: obj.execution_status,
    investigation_status: obj.investigation_status,
    quality_status: obj.quality_status,
    report_status: obj.report_status,
    rating_status: obj.rating_status,
    credit_status: obj.credit_status,
    requires_human_review: obj.requires_human_review,
    outstanding_issue_count: obj.outstanding_issue_count,
    restriction_reasons: [...obj.restriction_reasons],
  }
}

/** Writer 消息嵌在 content 中；终局及复核事件通常在顶层。 */
export function extractResearchOutcome(event: unknown): ResearchOutcome | null {
  const obj = record(event)
  if (!obj) return null
  return parseResearchOutcome(obj.research_outcome)
    ?? parseResearchOutcome(record(obj.content)?.research_outcome)
}

const executionLabels: Record<ResearchOutcome['execution_status'], string> = {
  running: '执行中', finished: '执行已结束', awaiting_review: '等待人工复核', cancelled: '已取消', degraded: '执行存在异常',
}
const investigationLabels: Record<ResearchOutcome['investigation_status'], string> = {
  not_started: '未开始', not_applicable: '不适用', running: '调查中', completed: '调查已结束',
  stalled: '连续未取得进展', time_limit: '已达时间上限', step_limit: '已达步骤上限', unknown: '尚未确认',
}
const qualityLabels: Record<ResearchOutcome['quality_status'], string> = {
  passed: '本轮质检通过', needs_revision: '待修订 / 复核', not_reviewed: '尚未完成质检',
}
const reportLabels: Record<ResearchOutcome['report_status'], string> = {
  unavailable: '尚无可交付报告', restricted: '受限草稿', partial: '部分材料报告', draft: '报告草稿', ready: '报告已生成',
}
const ratingLabels: Record<ResearchOutcome['rating_status'], string> = {
  not_evaluated: '尚未评估', insufficient: '依据不足', available: '已生成规则评级',
}

/** 仅展示后端已确认的各维度，不通过前端合成风险等级或审批结论。 */
export function describeResearchOutcome(outcome: ResearchOutcome | null): {
  title: string; type: 'warning' | 'info'; explanation: string; dimensions: string[]
} {
  if (!outcome) return {
    title: '调查与质检状态尚未确认', type: 'info',
    explanation: '未收到完整的报告状态回执；已有报告或规则评级不代表调查与质检通过。', dimensions: [],
  }
  return {
    title: reportLabels[outcome.report_status],
    type: outcome.report_status === 'restricted' || outcome.report_status === 'partial' ? 'warning' : 'info',
    explanation: outcome.report_status === 'restricted'
      ? '执行结束不代表调查/质检通过。本报告仅作受限草稿，请先处理下列限制；规则评级和额度不等于授信审批。'
      : outcome.report_status === 'partial'
        ? '本报告仅覆盖已取得的部分材料；未覆盖事项不能视为没有风险，规则评级和额度不等于授信审批。'
        : '执行、调查、质检与规则评级分别列示；报告生成不代表授信审批或放款。',
    dimensions: [
      `执行：${executionLabels[outcome.execution_status]}`,
      `调查：${investigationLabels[outcome.investigation_status]}`,
      `质检：${qualityLabels[outcome.quality_status]}`,
      `评级：${ratingLabels[outcome.rating_status]}`,
      `额度测算：${outcome.credit_status === 'available' ? '可用' : '不可用'}`,
      `人工复核：${outcome.requires_human_review ? '要求复核' : '非强制'}`,
      `未解决问题：${outcome.outstanding_issue_count}`,
    ],
  }
}
