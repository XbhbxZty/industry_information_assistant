import { request } from './request'

export type ScenarioKey = string
export type FieldSourceKind = 'official' | 'authorized' | 'audited'
export type MaterialSourceKind = 'company_submitted' | 'admin_observation'

export type ProfileFieldValue = string | number | boolean | null

export interface TemplateField {
  field_id: string
  field_name: string
  category: string
  required: boolean
  description: string
  scope?: 'core' | `scenario:${string}`
  input_type?: 'text' | 'number'
}

export interface CompanyProfileTemplate {
  version: string
  core: TemplateField[]
  scenarios: Record<string, TemplateField[]>
  scenario_options?: ScenarioKey[]
  scenario_data_keys?: Record<string, string[]>
  profile?: CompanyProfileData
  field_source?: Partial<FieldSource>
  material?: Partial<SupplementalMaterial>
  coverage?: string
  required?: string
}

export interface FieldSource {
  source_id: string
  name: string
  issuer: string
  source_type: FieldSourceKind
  field_ids: string[]
  retrieved_at: string
  as_of_date: string
  reference: string
  sha256?: string | null
}

export interface SupplementalMaterial {
  material_id?: string
  source_type: MaterialSourceKind
  title: string
  content: string
  reference?: string | null
  as_of_date?: string | null
  date_unknown_reason?: string | null
  eligible_for_structured_evidence?: false
}

/** 服务端只在读取/检索材料时标注其不可作为结构化证据；写入时不提交该派生字段。 */
export type SupplementalMaterialInput = Omit<SupplementalMaterial, 'eligible_for_structured_evidence'>

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
  scenario_data: Record<string, ProfileFieldValue>
  field_sources: FieldSource[]
  materials: SupplementalMaterialInput[]
}

export interface CompanyProfileSummary {
  id: string
  name: string
  credit_code?: string
  status: string
  scenario: ScenarioKey
  revision: number
  content_sha256: string
  updated_at: string
  updated_by?: string | null
  archived_at?: string | null
}

export interface CompanyProfile extends CompanyProfileSummary, Omit<CompanyProfileDraft, 'materials'> {
  materials: SupplementalMaterial[]
  created_at: string
  created_by?: string | null
  archived_at?: string | null
  archived_by?: string | null
}

export interface CompanyProfileListResponse {
  items: CompanyProfileSummary[]
  total: number
  offset: number
  limit: number
}

export interface CompanyProfileHistoryItem {
  id: string
  profile_id: string
  revision: number
  action: string
  actor_id?: string | null
  change_reason: string
  before_snapshot?: Record<string, unknown> | null
  after_snapshot: Record<string, unknown>
  content_sha256: string
  created_at: string
}

export interface CompanyProfileHistoryResponse {
  items: CompanyProfileHistoryItem[]
  total: number
}

export interface MaterialSearchResponse {
  items: SupplementalMaterial[]
  total: number
}

export interface CompanyProfileArchiveRequest {
  expected_revision: number
  change_reason: string
}

export function getCompanyProfileTemplate() {
  return request.get<CompanyProfileTemplate>('/company-profiles/templates', { loading: false })
}

export function getCompanyProfiles(params?: { query?: string; include_archived?: boolean; offset?: number; limit?: number }) {
  return request.get<CompanyProfileListResponse>('/company-profiles', { params, loading: false })
}

export function getCompanyProfile(id: string) {
  return request.get<CompanyProfile>(`/company-profiles/${id}`, {
    loading: false,
    responseStatusIsResourceState: true,
  })
}

export function createCompanyProfile(payload: CompanyProfileDraft) {
  return request.post<CompanyProfile>('/company-profiles', payload, {
    loading: false,
    responseStatusIsResourceState: true,
  })
}

export function updateCompanyProfile(
  id: string,
  payload: CompanyProfileDraft & { expected_revision: number; change_reason: string },
) {
  return request.put<CompanyProfile>(`/company-profiles/${id}`, payload, {
    loading: false,
    responseStatusIsResourceState: true,
  })
}

export function archiveCompanyProfile(id: string, payload: CompanyProfileArchiveRequest) {
  return request.post<CompanyProfile>(`/company-profiles/${id}/archive`, payload, {
    loading: false,
    responseStatusIsResourceState: true,
  })
}

export function getCompanyProfileHistory(id: string) {
  return request.get<CompanyProfileHistoryResponse>(`/company-profiles/${id}/history`, { loading: false })
}

export function searchCompanyProfileMaterials(id: string, query: string, limit?: number) {
  return request.post<MaterialSearchResponse>(`/company-profiles/${id}/materials/search`, { query, limit }, { loading: false })
}
