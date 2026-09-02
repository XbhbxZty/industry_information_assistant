// Focused API regression: node --test tests/company-profile-response.test.mjs
// Uses existing TypeScript + Node tooling; no browser or extra test framework.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

const require = createRequire(import.meta.url)
const serviceSource = readFileSync(new URL('../src/api/request/plugins/service.ts', import.meta.url), 'utf8')
const companyProfilesSource = readFileSync(new URL('../src/api/company-profiles.ts', import.meta.url), 'utf8')

function compile(source) {
  return ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
}

function responseInterceptors() {
  let fulfilled
  let rejected
  const module = { exports: {} }
  class ResponseError extends Error {
    constructor(message, response) {
      super(message)
      this.response = response
    }
  }
  runInNewContext(compile(serviceSource), {
    module,
    exports: module.exports,
    Promise,
    require: name => {
      if (name === '../error') return { ResponseError }
      if (name === './plugin') return {}
      return require(name)
    },
  })
  module.exports.servicePlugin.install({
    interceptors: { response: { use: (success, failure) => { fulfilled = success; rejected = failure } } },
  })
  return { fulfilled, rejected, ResponseError }
}

function companyProfiles(request) {
  const module = { exports: {} }
  runInNewContext(compile(companyProfilesSource), {
    module,
    exports: module.exports,
    require: name => name === './request' ? { request } : require(name),
  })
  return module.exports
}

test('company profile resource states pass through while legacy envelopes still reject', async () => {
  const { fulfilled, ResponseError } = responseInterceptors()
  for (const status of ['active', 'archived']) {
    const response = { data: { id: 'profile-1', status }, config: { responseStatusIsResourceState: true } }
    assert.equal(await fulfilled(response), response)
  }

  const legacyError = { data: { status: 'error', message: 'legacy API failure' }, config: {} }
  await assert.rejects(fulfilled(legacyError), error => {
    assert.ok(error instanceof ResponseError)
    assert.equal(error.message, 'legacy API failure')
    return true
  })
})

test('company profile HTTP errors remain their original Axios errors', async () => {
  const { rejected } = responseInterceptors()
  for (const status of [403, 409, 422]) {
    const error = { response: { status, data: { status: 'error', detail: 'server rejection' }, config: { responseStatusIsResourceState: true } } }
    await assert.rejects(rejected(error), actual => actual === error)
  }
})

test('only CompanyProfile detail and mutations opt into resource status handling', () => {
  const calls = []
  const request = {
    get: (...args) => { calls.push(['get', ...args]) },
    post: (...args) => { calls.push(['post', ...args]) },
    put: (...args) => { calls.push(['put', ...args]) },
  }
  const api = companyProfiles(request)
  const draft = { profile: { name: '测试企业' }, scenario: 'factoring', scenario_data: {}, field_sources: [], materials: [] }
  api.getCompanyProfile('profile-1')
  api.createCompanyProfile(draft)
  api.updateCompanyProfile('profile-1', { ...draft, expected_revision: 1, change_reason: '修订' })
  api.archiveCompanyProfile('profile-1', { expected_revision: 2, change_reason: '归档' })
  api.getCompanyProfileTemplate()
  api.getCompanyProfiles()
  api.getCompanyProfileHistory('profile-1')
  api.searchCompanyProfileMaterials('profile-1', '应收账款')

  assert.deepEqual(JSON.parse(JSON.stringify(calls.map(call => call.at(-1)))), [
    { loading: false, responseStatusIsResourceState: true },
    { loading: false, responseStatusIsResourceState: true },
    { loading: false, responseStatusIsResourceState: true },
    { loading: false, responseStatusIsResourceState: true },
    { loading: false },
    { loading: false },
    { loading: false },
    { loading: false },
  ])
})
