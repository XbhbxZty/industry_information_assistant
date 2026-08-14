// Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
// 未经授权，禁止转售或仿制。
//
// 本文件在原课程项目基础上二次开发（已获授权）。
// 改造部分 © 2026 XbhbxZty
/**
 * 全局行业状态管理
 */
import { proxy, subscribe } from 'valtio'

// 行业配置类型
export interface IndustryConfig {
  id: string
  name: string
  description: string
  // 资讯搜索关键词
  newsKeywords: string[]
  // 招投标搜索关键词
  biddingKeywords: string[]
  // 研究相关关键词
  researchKeywords: string[]
}

// 预定义的行业配置
// 业务线配置。
//
// 原项目这里是"行业"（智慧交通/金融科技/…），用于切换研究主题。
// 迁移到贷前尽调后改为**授信业务线**：不同业务线的尽调侧重不同，
// 保理看应收账款质量与买方资信，小微信贷看经营流水与实控人，
// 供应链金融看核心企业与上下游关系。
export const INDUSTRY_CONFIGS: IndustryConfig[] = [
  {
    id: 'supply_chain_finance',
    name: '供应链金融',
    description: '围绕核心企业的上下游融资，关注贸易背景真实性与关联关系',
    newsKeywords: ['供应链金融 政策', '核心企业 应付账款', '产业链融资', '票据 贴现'],
    biddingKeywords: ['供应链管理', '物流服务', '仓储配送'],
    researchKeywords: ['供应链金融', '核心企业', '贸易背景', '上下游'],
  },
  {
    id: 'micro_loan',
    name: '小微信贷',
    description: '面向小微企业的流动资金贷款，关注经营真实性与实际控制人',
    newsKeywords: ['小微企业 融资', '普惠金融 政策', '经营性贷款', '实际控制人 认定'],
    biddingKeywords: ['小微企业', '中小企业服务'],
    researchKeywords: ['小微信贷', '流动资金贷款', '实际控制人', '经营真实性'],
  },
  {
    id: 'factoring',
    name: '商业保理',
    description: '应收账款转让融资，关注账款真实性、买方资信与回款路径',
    newsKeywords: ['商业保理 监管', '应收账款 融资', '保理 备案', '账款 确权'],
    biddingKeywords: ['应收账款', '保理服务'],
    researchKeywords: ['商业保理', '应收账款', '买方资信', '回款路径'],
  },
]

// 行业状态
export interface IndustryState {
  currentIndustryId: string
  industries: IndustryConfig[]
}

// 从 localStorage 读取
const DEFAULT_ID = 'supply_chain_finance'

const getStoredIndustryId = (): string => {
  if (typeof window === 'undefined') return DEFAULT_ID
  const stored = localStorage.getItem('selected_industry_id')
  // 存量浏览器里可能还留着迁移前的行业 id（如 smart_transportation）。
  // 不校验的话 find 返回 undefined，切换器的选中态会与实际展示的业务线不一致。
  const valid = INDUSTRY_CONFIGS.some((i) => i.id === stored)
  if (stored && !valid) {
    console.info('[business store] 忽略失效的历史业务线 id:', stored)
    localStorage.removeItem('selected_industry_id')
  }
  return valid ? (stored as string) : DEFAULT_ID
}

// 创建状态
export const industryState = proxy<IndustryState>({
  currentIndustryId: getStoredIndustryId(),
  industries: INDUSTRY_CONFIGS,
})

// 订阅变化，保存到 localStorage
subscribe(industryState, () => {
  if (typeof window !== 'undefined') {
    console.log('[industry store] 保存行业到 localStorage:', industryState.currentIndustryId)
    localStorage.setItem('selected_industry_id', industryState.currentIndustryId)
  }
})

// 获取当前行业配置
export const getCurrentIndustry = (): IndustryConfig => {
  const industry = industryState.industries.find(
    (i) => i.id === industryState.currentIndustryId
  )
  console.log('[industry store] 获取当前行业:', industry?.name)
  return industry || INDUSTRY_CONFIGS[0]
}

// 切换行业
export const setCurrentIndustry = (industryId: string) => {
  console.log('[industry store] 切换行业:', industryId)
  industryState.currentIndustryId = industryId
}

// 获取行业列表（用于选择器）
export const getIndustryOptions = () => {
  return industryState.industries.map((i) => ({
    value: i.id,
    label: i.name,
    description: i.description,
  }))
}
