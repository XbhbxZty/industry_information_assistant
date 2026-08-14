// Copyright © 2026 XbhbxZty
// 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
import { request } from './request'
import type { AxiosRequestConfig } from 'axios'

/** 核查项四态。含义严格互斥，前端呈现必须区分——尤其后两者不可混淆：
 *  verified + "经查询，无相关记录" = 已核实的正面结论，可支持授信
 *  unverified                    = 信息缺口，必须补查
 */
export type CheckStatus = 'verified' | 'unverified' | 'conflicting' | 'not_applicable'

export interface FieldCheck {
  field_id: string
  field_name: string
  category: string
  section_id: string
  required: boolean
  status: CheckStatus
  value: string | null
  failure_reason?: string
  conflict_detail?: { source: string; value: string }[]
  /** 溯源字段（v0.6 证据链）。缺失即为来源不明，界面须显式标注 */
  verification_origin?: string
  source_adapter?: string
  retrieved_at?: string
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
}

export interface CreditRecommendation {
  recommendable: boolean
  reason: string
  suggested_amount: number | null
  range_low: number | null
  range_high: number | null
  basis: { method: string; value: number; detail: string }[]
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
    comment: string
    override_level: string | null
    /** 规则引擎的原始结论。人工可以改判，但原始值永远保留——
     *  改写而不留痕，事后无法区分"规则算错了"和"人改过了" */
    engine_level: string
    reviewed_at: string
  }
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
}

export interface ReviewDecision {
  reviewer: string
  approved: boolean
  comment?: string
  /** 人工改判后的等级。留空表示沿用规则引擎结论 */
  override_level?: string | null
}

/** 发起尽调（SSE 流） */
export function startDueDiligence(
  params: { query: string; session_id?: string },
  options?: AxiosRequestConfig,
) {
  return request.post<ReadableStream>(
    '/research/stream',
    { ...params, search_modes: [], version: 'v2' },
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
  return request.post<ReadableStream>(`/research/review/${sessionId}`, decision, {
    headers: { Accept: 'text/event-stream' },
    responseType: 'stream',
    adapter: 'fetch',
    loading: false,
    ...options,
  })
}
