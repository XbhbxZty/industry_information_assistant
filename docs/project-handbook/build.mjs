// Offline documentation build. Reads committed, allowlisted source only.
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const folder = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(folder, '../..')
export const revision = 'f5f8d17b4d257c2c698466962b001d0f6996e2ae'
const service = 'backend/app/service/admin_company_profile_service.py'
const graph = 'backend/app/service/deep_research_v2/graph.py'
const state = 'backend/app/service/deep_research_v2/state.py'
const router = 'backend/app/router/research_router.py'
const editor = 'frontend/src/pages/company-profiles/editor.tsx'
const refs = [
  ['src-form', '表单：场景与必查项', 'frontend/src/pages/company-profiles/ProfileForm.tsx', 'export function ProfileForm(', 104],
  ['src-normalise', '表单提交前的规范化', editor, 'function normaliseDraft(', 26],
  ['src-submit', '新建与修改的提交分支', editor, '  const submit = async ', 26],
  ['src-api', '四个档案资源接口', 'frontend/src/api/company-profiles.ts', 'export function getCompanyProfile(id:', 38],
  ['src-interceptor', '资源状态不是 API 成败状态', 'frontend/src/api/request/plugins/service.ts', 'export const servicePlugin:', 39],
  ['src-auth', '后端登录与管理员判定', 'backend/app/router/auth_router.py', 'async def get_current_user_required(', 43],
  ['src-schema', '写入请求与修改版本约束', 'backend/app/schemas/company_profile.py', 'class CompanyProfileWrite(', 46],
  ['src-source-schema', '字段来源和补充材料的不同类型', 'backend/app/schemas/company_profile.py', 'class FieldSourceInput(', 39],
  ['src-profile-route', '建档与详情接口的依赖和响应投影', 'backend/app/router/company_profile_router.py', '@router.post("", response_model=', 33],
  ['src-prepare', '内容准备：规范化、来源覆盖、确定性映射', service, 'def prepare_company_profile_content(', 83],
  ['src-source-gate', '信用代码规范化与来源记录校验', service, 'def _normalise_sources(', 64, 9],
  ['src-material-gate', '补充材料校验与非评分标志', service, 'def _normalise_materials(', 41],
  ['src-create', '创建：当前行与审计在同一事务内写入', service, 'def create_company_profile(', 32],
  ['src-audit-write', '签发审计并更新链头', service, 'def _append_audit(', 32],
  ['src-audit-snapshot', '完整审计快照的字段', service, 'def _row_snapshot(', 17],
  ['src-audit-hash', '审计快照摘要与内容摘要的区别', 'backend/app/service/company_profile_audit_integrity.py', 'def snapshot_sha256(', 8],
  ['src-audit-read', '读取时核对结构和签名历史', service, 'def validate_company_profile_audit_history(', 51],
  ['src-update', '修改：版本比较与条件更新', service, 'def update_company_profile(', 46],
  ['src-archive', '归档是终态，不是物理删除', service, 'def archive_company_profile(', 50],
  ['src-model', '档案当前行与审计表', 'backend/app/models/company_profile.py', 'class AdminCompanyProfile(Base):', 94],
  ['src-launch', '普通用户选择企业档案并发起', 'frontend/src/pages/due-diligence/index.tsx', 'export default function DueDiligencePage()', 39],
  ['src-stream-client', '前端流请求：传档案 ID，不传档案正文', 'frontend/src/pages/due-diligence/useDDStream.ts', '        company_profile_id: options?.companyProfileId,', 15, 18],
  ['src-snapshot-route', '流开始前读取、拒绝错误并转为纯 JSON', router, 'def _load_admin_company_profile_snapshot(', 74],
  ['src-stream-route', 'V2 接入快照与知识库授权范围', router, '        admin_snapshot = None', 39, 1],
  ['src-snapshot', '构造研究快照：内容与公开引用分离', service, 'def get_active_profile_snapshot(', 39],
  ['src-initial', '初始化共享状态并冻结快照', state, 'def create_initial_state(', 117],
  ['src-binding', '快照绑定的签发与验签', state, 'def create_admin_profile_snapshot_binding(', 75],
  ['src-profile-check', '运行时快照校验：两种哈希不能混用', 'backend/app/service/company_profile.py', 'def validate_admin_company_profile_snapshot(', 80],
  ['src-graph-load', '显式档案优先，之后复用事实、清单与 Adapter', graph, '        provided_profile = state.get("provided_company_profile") or {}', 119],
  ['src-graph-restore', '恢复管理档案时检查冻结快照与派生状态', graph, '    def _restore_managed_profile_snapshot(', 229],
  ['src-checkpoint-save', '图封签后交给业务检查点服务', graph, '    def _save_checkpoint(', 35],
  ['src-fieldcheck', 'FieldCheck：逐项核实状态', state, 'class FieldCheck(TypedDict):', 40],
  ['src-material-search', '档案材料当前只是隔离关键词检索', service, 'def search_profile_materials(', 26],
  ['src-process-test', '真实进程测试：创建、版本冻结、重启与复核', 'backend/tests/test_process_acceptance_postgres.py', 'def test_real_login_profile_workflow_freezing_review_and_archive(', 76],
  ['src-provenance-test', '测试：来源覆盖和空结果语义', 'backend/tests/test_admin_company_profiles.py', 'def test_source_gate_rebuilds_coverage_and_preserves_empty_semantics(', 43],
  ['src-badcase', '已记录的前端资源状态误判', 'docs/DEVELOPMENT_TRACE.md', '### DEV-BC-20260902-028', 10],
  ['src-scope', '阶段验收范围与下一步', 'docs/ADMIN_COMPANY_PROFILE_PLAN.md', '#### 2026-09-02 3.5 交付与恢复点', 30],
]

const escape = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;')
const files = new Map()
const sources = refs.map(([id, title, file, needle, count, before = 0]) => {
  if (!files.has(file)) files.set(file, execFileSync('git', ['show', `${revision}:${file}`], { cwd: root, encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 }))
  const text = files.get(file)
  const lines = text.replaceAll('\r\n', '\n').split('\n')
  const matches = lines.flatMap((line, i) => line.includes(needle) ? [i] : [])
  if (matches.length !== 1) throw new Error(`${id}: expected one source anchor, found ${matches.length}: ${needle}`)
  const start = Math.max(0, matches[0] - before)
  const end = Math.min(lines.length, matches[0] + count)
  return { id, title, file, start: start + 1, end, sha256: createHash('sha256').update(text).digest('hex'), lines: lines.slice(start, end) }
})
const snippets = sources.map(source => `<details class="source" id="${source.id}" data-search data-keywords="${escape(source.file)} ${escape(source.title)}">
<summary>${escape(source.title)} <span class="source-path">${escape(source.file)}:${source.start}</span></summary>
<p class="muted">提交 ${revision.slice(0, 7)} · 以下是第 ${source.start}–${source.end} 行的局部源码，不是整个文件。源码注释可能包含历史描述，应结合本章核对。</p>
<button type="button" class="small" data-copy="${escape(source.file)}:${source.start}">复制路径与行号</button>
<pre class="source-code"><code>${source.lines.map((line, index) => `<span class="code-line" id="${source.id}-L${source.start + index}"><a class="line-no" href="#${source.id}-L${source.start + index}" aria-label="第 ${source.start + index} 行">${source.start + index}</a><span>${escape(line) || ' '}</span></span>`).join('\n')}</code></pre>
<p class="muted">该版本文件 SHA-256：<code>${source.sha256}</code></p></details>`).join('\n')
// Git on Windows may check these text artifacts out as CRLF. Only EOLs differ;
// normalize them without relaxing the content/source reproducibility check.
let result = readFileSync(path.join(folder, 'handbook.template.html'), 'utf8').replaceAll('\r\n', '\n')
for (const [token, replacement] of [['@@REVISION@@', revision], ['@@SHORT_REVISION@@', revision.slice(0, 7)], ['@@SOURCE_COUNT@@', String(sources.length)], ['@@SOURCES@@', snippets]]) {
  if (!result.includes(token)) throw new Error(`missing template token ${token}`)
  result = result.replaceAll(token, replacement)
}
if (process.argv.includes('--check')) {
  if (readFileSync(path.join(folder, 'index.html'), 'utf8').replaceAll('\r\n', '\n') !== result) throw new Error('index.html is stale; run node docs/project-handbook/build.mjs')
  console.log(`Build reproducible: ${sources.length} source snapshots at ${revision.slice(0, 7)}`)
} else {
  writeFileSync(path.join(folder, 'index.html'), result, 'utf8')
  console.log(`Built ${Buffer.byteLength(result)} bytes, ${sources.length} source snapshots at ${revision.slice(0, 7)}`)
}
