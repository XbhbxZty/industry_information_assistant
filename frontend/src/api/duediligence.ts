// Copyright © 2026 XbhbxZty
// 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
import { request } from './request'
import type { AxiosRequestConfig } from 'axios'

/** 核查项四态。含义严格互斥，前端呈现必须区分——尤其后两者不可混淆：
 *  verified + "经查询，无相关记录" = 已核实的正面结论，可支持授信
 *  unverified                    = 信息缺口，必须补查
 */
export type CheckStatus = 'verified' | 'unverified' | 'conflicting' | 'not_applicable'
export type CheckScope = 'core' | `scenario:${string}`

export interface FieldCheck {
  field_id: string
  field_name: string
  category: string
  section_id: string
  /** 核心主体项与业务场景项必须分区呈现、分开统计。 */
  scope?: CheckScope
  required: boolean
  status: CheckStatus
  value: string | null
  failure_reason?: string
  conflict_detail?: { source: string; value: string }[]
  /** 溯源字段（v0.6 证据链）。缺失即为来源不明，界面须显式标注 */
  verification_origin?: string
  source_adapter?: string
  retrieved_at?: string
  as_of_date?: string
  evidence_ids?: string[]
}

export interface Completeness {
  required_total: number
  required_verified: number
  verified_rate: number
  unverified_fields: string[]
  conflicting_fields: string[]
  capability_gaps?: string[]
  by_category?: Record<string, { total: number; verified: number; rate: number }>
  /** 场景清单覆盖率不参与核心主体核实率、闸门或评级。 */
  scenario?: {
    name: string
    total: number
    verified: number
    rate: number
    unverified_fields: string[]
  }
}

export interface CreditRecommendation {
  recommendable: boolean
  reason: string
  suggested_amount: number | null
  range_low: number | null
  range_high: number | null
  basis: { method: string; value: number; detail: string }[]
  /** 无法测算的口径及原因（BC-72）。**必须呈现**——少一个口径就是少一道
   *  上限约束，读者要能分辨"算出来不利"与"根本没算" */
  unavailable_bases?: { method: string; reason: string }[]
  deductions: { item: string; amount: number; detail: string }[]
  adjustments: { factor: string; multiplier: number; detail: string }[]
  application_amount: number | null
  conditions: string[]
  advice_text: string
}

export interface RiskAssessment {
  level: string
  composite_score: number
  gates_applied: string[]
  gate_kinds?: string[]
  triggered_rules: {
    dimension: string
    score: number
    detail: string
    field_id: string
    evidence: string[]
  }[]
  dimension_scores: Record<string, number>
  dimensions_excluded: string[]
  requires_human_review: boolean
  credit_advice: string
  completeness: Completeness
  credit_recommendation?: CreditRecommendation
  provenance_degradations?: { field_id: string; detail: string }[]
  human_review?: {
    completed: boolean
    approved: boolean
    reviewer: string
    reviewer_id?: string
    comment: string
    override_level: string | null
    /** 规则引擎的原始结论。人工可以改判，但原始值永远保留——
     *  改写而不留痕，事后无法区分"规则算错了"和"人改过了" */
    engine_level: string
    reviewed_at: string
  }
}

/** 调查层（B 层）图表的两种类别。**呈现上必须能一眼区分**——
 *  一张图比一句话权威得多，读者不会去核对趋势图下面的脚注。
 *  deterministic: 解析自已核实字段，与证据附录同源
 *  exploratory:   来自未通过证据闸门的调查材料，不作为授信依据 */
export type ChartClass = 'deterministic' | 'exploratory'

export interface InvestigationChart {
  id: string
  chart_class: ChartClass
  chart_type: 'line' | 'bar' | 'stacked_bar' | 'graph'
  /** 带徽标的标题（探索性图表以徽标开头） */
  title: string
  /** 不带徽标的原名，供图例等处使用 */
  plain_title: string
  subtitle?: string
  unit?: string
  series: { period: string; value: number; text?: string; total?: number; missing?: number }[]
  graph?: { nodes: Record<string, unknown>[]; edges: Record<string, unknown>[] }
  provenance: {
    field_id?: string
    field_name?: string
    source_value?: string
    evidence_ids?: string[]
    sources?: { title?: string; url?: string; published_at?: string; retrieved_at?: string }[]
  }
  note: string
}

export interface InvestigationFinding {
  claim: string
  dimension: string
  source: { title?: string; url?: string; publisher?: string; published_at?: string; retrieved_at?: string }
  subject_confirmed: boolean
  verified: boolean
}

/** B 层未能产出的部分。**必须呈现**——少一节的原因要写明是
 *  「查了没有」还是「没查成」，否则读者会以为这一节本来就不存在 */
export interface InvestigationFailure {
  stage: string
  kind: 'not_found' | 'error' | 'disabled'
  reason: string
  detail?: string
}

/** 探索性实体关系图谱。**每条边都带来源**——一条关系边就是一条断言，
 *  画成图之后比写成文字更容易被采信，因此它和文字发现走同一道准入 */
export interface InvestigationGraph {
  nodes: { id: string; label: string; is_subject?: boolean; exploratory?: boolean }[]
  edges: {
    source: string
    target: string
    relation: string
    exploratory?: boolean
    origin?: { title?: string; publisher?: string; published_at?: string; retrieved_at?: string }
  }[]
}

/** 调查层（B 层）。与 A 层物理隔离：不参与评级、额度与证据附录 */
export interface Investigation {
  enabled: boolean
  charts: InvestigationChart[]
  graph: InvestigationGraph
  findings: InvestigationFinding[]
  failures: InvestigationFailure[]
}

/** 管理端企业档案的不可变审计引用。运行期间只传递此公开标识，不传递档案内容。 */
export interface ProfileRef {
  id: string
  revision: number
  content_sha256: string
  source: 'admin_company_profile'
}

/** 复核卡片的载荷，由后端 interrupt payload 推出 */
export interface HumanReviewRequest {
  type: 'human_review_required'
  session_id: string
  company_name: string
  level: string
  composite_score: number
  credit_advice: string
  gates_applied: string[]
  verified_rate: number
  unverified_fields: string[]
  conflicting_fields: string[]
  critical_issues: { description?: string; issue_type?: string }[]
  errors: string[]
  /** 本次尽调冻结的管理端档案版本；无管理端档案时后端可省略。 */
  profile_ref?: ProfileRef | null
}

export interface ReviewDecision {
  approved: boolean
  comment?: string
  /** 人工改判后的等级。留空表示沿用规则引擎结论 */
  override_level?: string | null
}

/** reviewer 包中的额度建议投影；不复用完整的风险引擎内部对象。 */
export interface ReviewCreditRecommendation {
  recommendable?: boolean | null
  suggested_amount?: number | null
  currency?: string | null
  based_on_level?: string | null
  advice_text?: string | null
  conditions: string[]
}

/** 服务端显式构造的风险依据投影。 */
export interface ReviewRiskAssessment {
  level?: string | null
  composite_score?: number | null
  credit_advice?: string | null
  credit_recommendation?: ReviewCreditRecommendation | null
  gates_applied: string[]
  triggered_rules: string[]
}

/** 服务端显式构造的完整度投影。 */
export interface ReviewCompleteness {
  required_total?: number | null
  required_verified?: number | null
  verified_rate?: number | null
  unverified_fields: string[]
  conflicting_fields: string[]
}

export interface ReviewEvidenceSummary {
  evidence_id: string
  field_id?: string | null
  source_adapter?: string | null
  retrieved_at?: string | null
  as_of_date?: string | null
  active?: boolean | null
}

/** 待复核任务列表的最小投影。它不是检查点，绝不包含 state_json 或 ui_state_json。 */
export interface ReviewQueueItem {
  session_id: string
  company_name: string
  created_at?: string
  updated_at?: string
  profile_ref?: ProfileRef | null
  risk_assessment: ReviewRiskAssessment
  completeness: ReviewCompleteness
}

/** reviewer 专用详情包；字段只覆盖服务端显式允许公开的尽调材料。 */
export interface ReviewDetail extends ReviewQueueItem {
  final_report: string
  unverified_fields?: string[]
  conflicting_fields?: string[]
  critical_issues: { description?: string | null; issue_type?: string | null; severity?: string | null }[]
  errors: string[]
  evidence: ReviewEvidenceSummary[]
}

/** 仅具备 can_human_review 的独立复核人可以调用。 */
export function getReviewQueue() {
  return request.get<{ items: ReviewQueueItem[]; total: number }>('/research/reviews', { loading: false })
}

/** 返回单会话最小复核材料包，而不是完整检查点。 */
export function getReviewDetail(sessionId: string) {
  return request.get<ReviewDetail>(`/research/reviews/${sessionId}`, { loading: false })
}

/** 发起尽调（SSE 流） */
export function startDueDiligence(
  params: {
    query: string
    session_id?: string
    subject_name?: string
    company_profile_id?: string
    business_type?: string
    kb_name?: string
    as_of?: string
    search_modes?: ('web' | 'local')[]
    /** 调查层探索性抽取。留空 = 随尽调模式默认开启 */
    investigation?: boolean
  },
  options?: AxiosRequestConfig,
) {
  return request.post<ReadableStream<Uint8Array>>(
    '/research/stream',
    {
      ...params,
      search_modes: params.search_modes ?? [],
      due_diligence: true,
      version: 'v2',
    },
    {
      headers: { Accept: 'text/event-stream' },
      responseType: 'stream',
      adapter: 'fetch',
      loading: false,
      ...options,
    },
  )
}

/** 提交风控复核结论，从断点继续（SSE 流） */
export function submitReview(
  sessionId: string,
  decision: ReviewDecision,
  options?: AxiosRequestConfig,
) {
  return request.post<ReadableStream<Uint8Array>>(`/research/review/${sessionId}`, decision, {
    headers: { Accept: 'text/event-stream' },
    responseType: 'stream',
    adapter: 'fetch',
    loading: false,
    ...options,
  })
}
