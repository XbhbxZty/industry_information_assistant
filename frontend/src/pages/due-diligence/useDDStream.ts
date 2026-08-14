// Copyright © 2026 XbhbxZty
// 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
import { useCallback, useRef, useState } from 'react'
import {
  startDueDiligence,
  submitReview,
  type Completeness,
  type FieldCheck,
  type HumanReviewRequest,
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
  report: string
  /** 证据链降级与执行错误。**必须呈现**——只写进日志的话，
   *  复核人不会知道这份结论建立在来源不明的数据上 */
  errors: string[]
  errorMessage: string
}

const EMPTY: DDState = {
  phase: 'idle', sessionId: '', companyName: '', stage: '',
  steps: [], fieldChecks: [], completeness: null, risk: null,
  reviewRequest: null, report: '', errors: [], errorMessage: '',
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

  const handleEvent = useCallback((json: any) => {
    const t = json?.type
    if (!t) return

    switch (t) {
      case 'phase':
        patch({ stage: json.content || json.phase || '' })
        break

      case 'company_profile_loaded':
        patch({ companyName: json.company_name || json.content?.company_name || '' })
        break

      case 'field_checks_updated':
        patch({
          fieldChecks: json.field_checks || [],
          completeness: json.completeness || null,
        })
        break

      case 'research_step':
      case 'action': {
        const c = json.content || json
        if (c?.title) {
          setState(prev => ({
            ...prev,
            steps: [...prev.steps, { title: c.title, detail: c.subtitle, at: Date.now() }],
          }))
        }
        break
      }

      case 'risk_assessment': {
        const c = json.content || json
        setState(prev => ({
          ...prev,
          risk: { ...(prev.risk || {}), ...c } as RiskAssessment,
        }))
        break
      }

      // —— 复核卡点。暂停 ≠ 完成，界面必须显式区分 ——
      case 'human_review_required':
        patch({ phase: 'awaiting_review', reviewRequest: json as HumanReviewRequest })
        break

      case 'research_resumed':
        patch({ phase: 'running', stage: '已提交复核结论，从断点继续…' })
        break

      case 'human_review_completed':
        setState(prev => ({
          ...prev,
          risk: prev.risk
            ? { ...prev.risk, human_review: json.human_review, level: json.level,
                gates_applied: json.gates_applied, credit_advice: json.credit_advice }
            : prev.risk,
        }))
        break

      case 'research_complete':
        patch({
          phase: 'completed',
          report: json.final_report || '',
          fieldChecks: json.field_checks || [],
          completeness: json.completeness || null,
          risk: json.risk_assessment || null,
          errors: json.errors || [],
          reviewRequest: null,
        })
        break

      case 'research_cancelled':
        patch({ phase: 'cancelled', stage: '已取消' })
        break

      case 'error':
        patch({ phase: 'error', errorMessage: String(json.content || '未知错误') })
        break
    }
  }, [patch])

  /** 读取一条 SSE 流直到结束。两个入口（发起 / 恢复复核）共用 */
  const consume = useCallback(async (body: ReadableStream) => {
    const reader = (body as any).getReader()
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

  const start = useCallback(async (query: string) => {
    const sessionId = `dd-${Date.now()}`
    abortRef.current?.abort()
    abortRef.current = new AbortController()
    setState({ ...EMPTY, phase: 'running', sessionId, stage: '正在载入企业档案…' })
    try {
      const res = await startDueDiligence({ query, session_id: sessionId },
        { signal: abortRef.current.signal } as any)
      await consume(res.data as any)
      // 流结束但既未完成也未暂停：按未完成处理，不假装成功
      setState(prev =>
        prev.phase === 'running'
          ? { ...prev, phase: 'error', errorMessage: '连接中断，本次尽调未产出结论' }
          : prev)
    } catch (e: any) {
      if (e?.name !== 'CanceledError' && e?.name !== 'AbortError') {
        patch({ phase: 'error', errorMessage: e?.message || '请求失败' })
      }
    }
  }, [consume, patch])

  const review = useCallback(async (decision: ReviewDecision) => {
    const sid = state.sessionId
    if (!sid) return
    patch({ phase: 'running', stage: '正在提交复核结论…' })
    try {
      const res = await submitReview(sid, decision)
      await consume(res.data as any)
    } catch (e: any) {
      patch({ phase: 'error', errorMessage: e?.message || '提交复核失败' })
    }
  }, [state.sessionId, consume, patch])

  const reset = useCallback(() => {
    abortRef.current?.abort()
    setState(EMPTY)
  }, [])

  return { state, start, review, reset }
}
