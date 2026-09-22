import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

const code = ts.transpileModule(readFileSync(new URL('../src/api/request/plugins/auth.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText

function setup() {
  const state = { token: 'current' }
  let request, reject, logouts = 0
  const module = { exports: {} }
  runInNewContext(code, { module, exports: module.exports,
    window: { $app: { message: { warning() {} } } },
    require: () => ({ authState: state, authActions: { logout() { logouts++; state.token = null } } }),
  })
  module.exports.authPlugin.preinstall({ interceptors: {
    request: { use(fn) { request = fn } }, response: { use(_, fn) { reject = fn } },
  } })
  return { state, request, reject, logouts: () => logouts }
}

test('request uses current in-memory token immediately after login', () => {
  const s = setup()
  s.state.token = 'new'
  assert.equal(s.request({ headers: {} }).headers.Authorization, 'Bearer new')
})

for (const [label, status, token, url, expected] of [
  ['expired session', 401, 'current', '/knowledge-bases', 1],
  ['late old request', 401, 'old', '/knowledge-bases', 0],
  ['wrong password', 401, 'current', '/auth/login', 0],
  ['forbidden', 403, 'current', '/company-profiles', 0],
  ['server failure', 500, 'current', '/auth/me', 0],
]) {
  test(label, async () => {
    const s = setup()
    const error = { response: { status, config: { url, headers: { Authorization: `Bearer ${token}` } } } }
    await assert.rejects(s.reject(error), e => e === error)
    assert.equal(s.logouts(), expected)
    if (expected) {
      await assert.rejects(s.reject(error))
      assert.equal(s.logouts(), 1)
    }
  })
}
