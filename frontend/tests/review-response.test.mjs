// Focused API regression: node --test tests/review-response.test.mjs
// Uses existing TypeScript + Node tooling; no browser or extra test framework.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import { runInNewContext } from 'node:vm'
import { AxiosError } from 'axios'
import ts from 'typescript'

const require = createRequire(import.meta.url)
const source = readFileSync(new URL('../src/api/duediligence.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

function api(post) {
  const module = { exports: {} }
  runInNewContext(compiled, {
    module, exports: module.exports, ReadableStream, Response,
    require: name => name === './request' ? { request: { post } } : require(name),
  })
  return module.exports
}

test('successful SSE remains the original unread stream', async () => {
  const stream = new Response('data: {"type":"research_complete"}\n\n').body
  const response = { data: stream }
  const client = api(async (url, decision, config) => {
    assert.equal(url, '/research/review/session-one')
    assert.equal(decision.approved, true)
    assert.equal(config.responseType, 'stream')
    return response
  })
  assert.equal(await client.submitReview('session-one', { approved: true }), response)
  assert.equal(stream.locked, false)
  assert.match(await new Response(stream).text(), /research_complete/)
})

for (const [status, data] of [
  [409, { detail: '该任务已接受另一份决定，不能覆盖；请使用原决定重试' }],
  [422, { detail: [{ msg: 'strict boolean required' }] }],
]) {
  test(`pre-SSE ${status} JSON is decoded without changing the HTTP error`, async () => {
    const response = { status, data: new Response(JSON.stringify(data)).body }
    const error = new AxiosError('request failed', 'ERR_BAD_REQUEST', undefined, undefined, response)
    const client = api(async () => { throw error })
    await assert.rejects(client.submitReview('session-one', { approved: true }), actual => {
      assert.equal(actual, error)
      assert.equal(actual.response.status, status)
      assert.deepEqual(actual.response.data, data)
      return true
    })
  })
}

test('non-JSON proxy failure preserves its HTTP status', async () => {
  const response = { status: 503, data: new Response('<html>Unavailable</html>').body }
  const error = new AxiosError('request failed', 'ERR_BAD_RESPONSE', undefined, undefined, response)
  const client = api(async () => { throw error })
  await assert.rejects(client.submitReview('session-one', { approved: true }), actual => {
    assert.equal(actual, error)
    assert.equal(actual.response.status, 503)
    return true
  })
})

test('network errors without a response remain retryable errors', async () => {
  const error = new AxiosError('Network Error', 'ERR_NETWORK')
  const client = api(async () => { throw error })
  await assert.rejects(client.submitReview('session-one', { approved: true }), actual => actual === error)
})
