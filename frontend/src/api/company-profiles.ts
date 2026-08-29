import { request } from './request'

export type ScenarioKey = '' | 'factoring'
export type FieldSourceKind = 'official' | 'authorized' | 'audited'

export interface TemplateField {
  field_id: string
  field_name: string
  category: string
  required: boolean
  description: string
  scope?: 'core' | `scenario:${string}`
}

export interface CompanyProfileTemplate {
  version?: string
  core: TemplateField[]
  scenarios: Record<string, TemplateField[]>
}

export interface FieldSource {
  field_ids: string[]
  source: FieldSourceKind
  date: string
  reference?: string
}

export interface SupplementalMaterial {
  title: string
  content: string
}

export type ProfileFieldValue = string | number | boolean | undefined

export interface CompanyProfileData {
  name: string
  credit_code?: string
  registration?: Record<string, ProfileFieldValue>
  shareholders?: Record<string, ProfileFieldValue>[]
  actual_controller?: Record<string, ProfileFieldValue>
  external_investment?: Record<string, ProfileFieldValue>[]
  financials?: Record<string, ProfileFieldValue>[]
  judicial_records?: Record<string, ProfileFieldValue>[]
  guarantee?: Record<string, ProfileFieldValue>[]
  related_party?: Record<string, ProfileFieldValue>[]
  bidding_records?: Record<string, ProfileFieldValue>[]
  negative_news?: Record<string, ProfileFieldValue>[]
  regulatory_penalty?: Record<string, ProfileFieldValue>[]
  credit_application?: Record<string, ProfileFieldValue>
}

export interface CompanyProfileDraft {
  profile: CompanyProfileData
  scenario: ScenarioKey
  scenario_data: Record<string, Record<string, ProfileFieldValue>>
  field_sources: FieldSource[]
  materials: SupplementalMaterial[]
}

export interface CompanyProfileSummary {
  id: string
  name: string
  credit_code?: string
  scenario: ScenarioKey
  revision: number
  updated_at?: string
  archived_at?: string | null
}

export interface CompanyProfile extends CompanyProfileSummary, CompanyProfileDraft {
  created_at?: string
  created_by?: string
  updated_by?: string
}

export interface CompanyProfileListResponse {
  items: CompanyProfileSummary[]
  total: number
}

export interface CompanyProfileHistoryItem {
  revision: number
  changed_at: string
  changed_by?: string
  change_reason?: string
}

export function getCompanyProfileTemplate() {
  return request.get<CompanyProfileTemplate>('/company-profiles/templates', { loading: false })
}

export function getCompanyProfiles(params?: { keyword?: string; page?: number; page_size?: number }) {
  return request.get<CompanyProfileListResponse>('/company-profiles', { params, loading: false })
}

export function getCompanyProfile(id: string) {
  return request.get<CompanyProfile>(`/company-profiles/${id}`, { loading: false })
}

export function createCompanyProfile(payload: CompanyProfileDraft) {
  return request.post<CompanyProfile>('/company-profiles', payload, { loading: false })
}

export function updateCompanyProfile(
  id: string,
  payload: CompanyProfileDraft & { expected_revision: number; change_reason?: string },
) {
  return request.put<CompanyProfile>(`/company-profiles/${id}`, payload, { loading: false })
}

export function archiveCompanyProfile(id: string, changeReason?: string) {
  return request.post<CompanyProfile>(`/company-profiles/${id}/archive`, { change_reason: changeReason }, { loading: false })
}

export function getCompanyProfileHistory(id: string) {
  return request.get<CompanyProfileHistoryItem[]>(`/company-profiles/${id}/history`, { loading: false })
}

export function searchCompanyProfileMaterials(id: string, query: string) {
  return request.post<Record<string, unknown>[]>(`/company-profiles/${id}/materials/search`, { query }, { loading: false })
}
