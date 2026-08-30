// Copyright © 2026 XbhbxZty
// 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
import { useCallback, useRef, useState } from 'react'
import {
  startDueDiligence,
  submitReview,
  type Completeness,
  type FieldCheck,
  type HumanReviewRequest,
  type Investigation,
  type ProfileRef,
  type ReviewDecision,
  type RiskAssessment,
} from '@/api/duediligence'

export type DDPhase =
  | 'idle'
  | 'running'
  /** 已在复核卡点暂停。**这不是完成态**——此时的结论尚未生效 */
  | 'awaiting_review'
  | 'completed'
  | 'error'
  | 'cancelled'

export interface DDState {
  phase: DDPhase
  sessionId: string
  companyName: string
  /** 后端推来的阶段说明，用于进度条文案 */
  stage: string
  steps: { title: string; detail?: string; at: number }[]
  fieldChecks: FieldCheck[]
  completeness: Completeness | null
  risk: RiskAssessment | null
  reviewRequest: HumanReviewRequest | null
  /** 本次运行冻结的管理端档案审计引用；仅新任务或显式重置时清空。 */
  profileRef: ProfileRef | null
  report: string
  /** 调查层（B 层）。**与上面的 risk / completeness 语义完全隔离**：
   *  它不参与评级与额度，界面上也必须分区呈现，不得与清单混排 */
  investigation: Investigation | null
  /** 证据链降级与执行错误。**必须呈现**——只写进日志的话，
   *  复核人不会知道这份结论建立在来源不明的数据上 */
  errors: string[]
  errorMessage: string
}

const EMPTY: DDState = {
  phase: 'idle', sessionId: '', companyName: '', stage: '',
  steps: [], fieldChecks: [], completeness: null, risk: null,
  reviewRequest: null, profileRef: null, report: '', investigation: null,
  errors: [], errorMessage: '',
}

interface DDStreamEvent {
  type?: string
  session_id?: string
  name?: string
  message?: string
  content?: unknown
  phase?: string
  company_name?: string
  field_checks?: FieldCheck[]
  completeness?: Completeness
  title?: string
  subtitle?: string
  investigation?: Investigation
  human_review?: RiskAssessment['human_review']
  level?: string
  gates_applied?: string[]
  credit_advice?: string
  final_report?: string
  risk_assessment?: RiskAssessment
  errors?: string[]
  /** SSE 公开的冻结档案审计引用。 */
  profile_ref?: unknown
}

function asEvent(value: unknown): DDStreamEvent {
  return value !== null && typeof value === 'object' ? value as DDStreamEvent : {}
}

type UnknownRecord = Record<string, unknown>

function asRecord(value: unknown): UnknownRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as UnknownRecord
    : null
}

/**
 * SSE 是不可信 JSON。只接受字段齐全的公开审计引用，
 * 避免把半截对象写进恢复态后再由 UI 或复核请求继续传播。
 */
function asProfileRef(value: unknown): ProfileRef | null {
  const record = asRecord(value)
  if (
    !record
    || typeof record.id !== 'string'
    || !record.id.trim()
    || typeof record.revision !== 'number'
    || !Number.isSafeInteger(record.revision)
    || record.revision < 1
    || typeof record.content_sha256 !== 'string'
    || !/^[0-9a-f]{64}$/.test(record.content_sha256)
    || record.source !== 'admin_company_profile'
  ) return null

  return {
    id: record.id,
    revision: record.revision,
    content_sha256: record.content_sha256,
    source: record.source,
  }
}

function extractProfileRef(event: DDStreamEvent): ProfileRef | null {
  const nested = asRecord(event.content)
  return asProfileRef(event.profile_ref) ?? asProfileRef(nested?.profile_ref)
}

function eventPayload(event: DDStreamEvent): DDStreamEvent {
  return event.content !== null && typeof event.content === 'object'
    ? asEvent(event.content)
    : event
}

function errorInfo(value: unknown): { name: string; message: string } {
  if (value instanceof Error) return { name: value.name, message: value.message }
  const record = asEvent(value)
  return {
    name: typeof record.name === 'string' ? record.name : '',
    message: typeof record.message === 'string' ? record.message : '',
  }
}

/**
 * 消费尽调 SSE 流。
 *
 * 与聊天页的区别：尽调关心的是**结构化状态**（清单、核实率、评级、复核卡点），
 * 而不是逐 token 的文字流。因此这里只挑取决策相关的事件类型，
 * 其余一律忽略——把所有事件都渲染出来只会淹没真正要看的东西。
 */
export function useDDStream() {
  const [state, setState] = useState<DDState>(EMPTY)
  const abortRef = useRef<AbortController | null>(null)

  const patch = useCallback((p: Partial<DDState>) => {
    setState(prev => ({ ...prev, ...p }))
  }, [])

  const handleEvent = useCallback((value: unknown) => {
    const json = asEvent(value)
    const t = json?.type
    if (!t) return
    const profileRef = extractProfileRef(json)
    // profile_ref 是运行快照的一部分。事件没带该字段时只更新其它状态，
    // 绝不能把此前已恢复的引用误写成 null；新任务和 reset 才负责清空它。
    const patchWithProfileRef = (next: Partial<DDState>) => {
      patch(profileRef ? { ...next, profileRef } : next)
    }

    switch (t) {
      case 'research_start': {
        const c = eventPayload(json)
        const sessionId = typeof json.session_id === 'string'
          ? json.session_id
          : typeof c.session_id === 'string' ? c.session_id : undefined
        const companyName = typeof json.company_name === 'string'
          ? json.company_name
          : typeof c.company_name === 'string' ? c.company_name : undefined
        patchWithProfileRef({
          ...(sessionId ? { sessionId } : {}),
          ...(companyName ? { companyName } : {}),
        })
        break
      }

      case 'phase':
        patch({ stage: typeof json.content === 'string' ? json.content : json.phase || '' })
        break

      case 'company_profile_loaded': {
        const c = eventPayload(json)
        const companyName = typeof json.company_name === 'string'
          ? json.company_name
          : typeof c.company_name === 'string' ? c.company_name : undefined
        patchWithProfileRef(companyName ? { companyName } : {})
        break
      }

      case 'field_checks_updated':
        {
          const c = eventPayload(json)
        patch({
          fieldChecks: json.field_checks || c.field_checks || [],
          completeness: json.completeness || c.completeness || null,
        })
        }
        break

      case 'research_step':
      case 'action': {
        const c = eventPayload(json)
        // 先取到局部再判空：属性上的收窄不会带进闭包，
        // 直接写 c.title 会把 undefined 塞进必填字段
        const title = c?.title
        if (title) {
          setState(prev => ({
            ...prev,
            steps: [...prev.steps, { title, detail: c.subtitle, at: Date.now() }],
          }))
        }
        break
      }

      // ⚠️ 复核人签的是这份报告，暂停时必须看得到它。
      //    `research_complete` 在暂停时刻意不发（暂停 ≠ 完成），
      //    因此报告正文只能从撰写阶段的 report_draft 取——
      //    否则复核卡片弹出来时，右侧是空的，复核人只能凭等级和闸门签字。
      case 'report_draft': {
        const c = eventPayload(json)
        if (typeof c.content === 'string') patch({ report: c.content })
        break
      }

      // 「迭代用尽仍有阻断级问题，已强制转人工复核」这类提示必须呈现，
      // 它解释了复核卡点为什么会出现
      case 'warning': {
        const c = eventPayload(json)
        const text = typeof json.content === 'string'
          ? json.content
          : typeof c.content === 'string' ? c.content : ''
        if (text) {
          setState(prev =>
            prev.errors.includes(text) ? prev : { ...prev, errors: [...prev.errors, text] })
        }
        break
      }

      // 调查层。**不并入 risk**：它不参与裁决，混进同一个对象里
      // 迟早会有人顺手把它当成评级依据读出去。
      case 'investigation': {
        const c = eventPayload(json)
        patch({ investigation: c as unknown as Investigation })
        break
      }

      case 'risk_assessment': {
        const c = eventPayload(json)
        setState(prev => ({
          ...prev,
          risk: { ...(prev.risk || {}), ...c } as RiskAssessment,
        }))
        break
      }

      // —— 复核卡点。暂停 ≠ 完成，界面必须显式区分 ——
      case 'human_review_required': {
        const reviewPayload = eventPayload(json)
        // `json` 来自不可信 SSE；先移除原始字段，再只回填经过结构校验的 ref，
        // 防止半截 profile_ref 通过复核卡片继续扩散。
        const reviewRequestPayload = Object.fromEntries(
          Object.entries(reviewPayload).filter(([key]) => key !== 'profile_ref'),
        )
        patchWithProfileRef({
          phase: 'awaiting_review',
          reviewRequest: {
            ...(reviewRequestPayload as HumanReviewRequest),
            ...(profileRef ? { profile_ref: profileRef } : {}),
          },
        })
        break
      }

      case 'research_resumed': {
        const c = eventPayload(json)
        const sessionId = typeof json.session_id === 'string'
          ? json.session_id
          : typeof c.session_id === 'string' ? c.session_id : undefined
        patchWithProfileRef({
          phase: 'running',
          stage: '已提交复核结论，从断点继续…',
          ...(sessionId ? { sessionId } : {}),
        })
        break
      }

      // ⚠️ 事件里缺哪一项就保留原值，**不得写入 undefined**。
      //    等级、闸门、授信结论都是必填字段：写进 undefined 之后界面上
      //    是一片空白，而"复核完成后风险等级变成空的"会被读成没有风险。
      //    这与后端 `unratable()` 同一原则——宁可显示旧值，不给空结论。
      case 'human_review_completed':
        setState(prev => ({
          ...prev,
          risk: prev.risk
            ? {
                ...prev.risk,
                human_review: json.human_review ?? prev.risk.human_review,
                level: json.level ?? prev.risk.level,
                gates_applied: json.gates_applied ?? prev.risk.gates_applied,
                credit_advice: json.credit_advice ?? prev.risk.credit_advice,
              }
            : prev.risk,
        }))
        break

      case 'research_complete':
        setState(prev => ({
          ...prev,
          phase: 'completed',
          report: json.final_report || prev.report,
          fieldChecks: json.field_checks || prev.fieldChecks,
          completeness: json.completeness || prev.completeness,
          risk: json.risk_assessment || prev.risk,
          investigation: json.investigation || prev.investigation,
          // 必须与已累积的 warning 合并。终局事件只带 state["errors"]，
          // 流式过程中推来的告警不在其中，直接覆盖会让它们凭空消失。
          errors: Array.from(new Set([...prev.errors, ...(json.errors || [])])),
          reviewRequest: null,
          ...(profileRef ? { profileRef } : {}),
        }))
        break

      case 'research_cancelled':
        patch({ phase: 'cancelled', stage: '已取消' })
        break

      case 'error':
        patch({
          phase: 'error',
          errorMessage: typeof json.content === 'string' ? json.content : '未知错误',
        })
        break
    }
  }, [patch])

  /** 读取一条 SSE 流直到结束。两个入口（发起 / 恢复复核）共用 */
  const consume = useCallback(async (body: ReadableStream<Uint8Array>) => {
    const reader = body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const part of parts) {
        for (const line of part.split('\n')) {
          if (!line.startsWith('data: ')) continue
          const payload = line.slice(6).trim()
          if (!payload || payload === '[DONE]') continue
          try {
            handleEvent(JSON.parse(payload))
          } catch {
            // 单条事件解析失败不应中断整条流——后端偶发的半条 JSON
            // 不该让已经拿到的评级凭空消失
          }
        }
      }
    }
  }, [handleEvent])

  const start = useCallback(async (
    query: string,
    options?: {
      kbName?: string
      asOf?: string
      subjectName?: string
      businessType?: string
      companyProfileId?: string
    },
  ) => {
    const sessionId = `dd-${Date.now()}`
    abortRef.current?.abort()
    abortRef.current = new AbortController()
    setState({ ...EMPTY, phase: 'running', sessionId, stage: '正在载入企业档案…' })
    try {
      const res = await startDueDiligence({
        query,
        session_id: sessionId,
        subject_name: options?.subjectName,
        company_profile_id: options?.companyProfileId,
        business_type: options?.businessType,
        kb_name: options?.kbName,
        as_of: options?.asOf,
        search_modes: options?.kbName ? ['local'] : [],
      },
        { signal: abortRef.current.signal })
      await consume(res.data)
      // 流结束但既未完成也未暂停：按未完成处理，不假装成功
      setState(prev =>
        prev.phase === 'running'
          ? { ...prev, phase: 'error', errorMessage: '连接中断，本次尽调未产出结论' }
          : prev)
    } catch (e: unknown) {
      const error = errorInfo(e)
      if (error.name !== 'CanceledError' && error.name !== 'AbortError') {
        patch({ phase: 'error', errorMessage: error.message || '请求失败' })
      }
    }
  }, [consume, patch])

  const review = useCallback(async (decision: ReviewDecision) => {
    const sid = state.sessionId
    if (!sid) return
    patch({ phase: 'running', stage: '正在提交复核结论…' })
    try {
      const res = await submitReview(sid, decision)
      await consume(res.data)
    } catch (e: unknown) {
      patch({ phase: 'error', errorMessage: errorInfo(e).message || '提交复核失败' })
    }
  }, [state.sessionId, consume, patch])

  const reset = useCallback(() => {
    abortRef.current?.abort()
    setState(EMPTY)
  }, [])

  return { state, start, review, reset }
}
