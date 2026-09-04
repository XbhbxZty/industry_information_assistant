// Offline checks only: no browser, server, database, external assets or packages.
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'

const folder = path.dirname(fileURLToPath(import.meta.url))
const html = readFileSync(path.join(folder, 'index.html'), 'utf8')
let checks = 0
function check(name, fn) { fn(); checks++; console.log(`PASS ${name}`) }
const script = html.match(/<script id="handbook-script">([\s\S]*?)<\/script>/)?.[1]
const css = html.match(/<style>([\s\S]*?)<\/style>/)?.[1]
assert.ok(script && css)
// This deliberately checks this document's explicit markup, not arbitrary HTML5.
const markup = html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '').replace(/<style>[\s\S]*?<\/style>/g, '')
const tags = [...markup.matchAll(/<([a-zA-Z][\w:-]*)\b([^>]*)>/g)]
const ids = tags.flatMap(tag => [...tag[2].matchAll(/\bid="([^"]+)"/g)].map(match => match[1]))
const idSet = new Set(ids)

check('unique IDs and complete internal links', () => {
  assert.equal(ids.length, idSet.size, 'duplicate ID')
  for (const tag of tags) for (const match of tag[2].matchAll(/\bhref="([^"]+)"/g)) {
    assert.ok(match[1].startsWith('#'), `non-local link: ${match[1]}`)
    assert.ok(idSet.has(match[1].slice(1)), `missing target: ${match[1]}`)
  }
  for (const match of html.matchAll(/(?:aria-labelledby|aria-describedby)="([^"]+)"/g)) {
    for (const id of match[1].split(/\s+/)) assert.ok(idSet.has(id), `missing accessible label: ${id}`)
  }
})
check('explicit tag nesting and closed elements', () => {
  const stack = []
  const voids = new Set(['meta', 'link', 'input', 'br', 'hr', 'img', 'source', 'wbr', 'area', 'base', 'embed', 'param', 'track', 'col'])
  for (const tag of markup.matchAll(/<(\/?)([a-zA-Z][\w:-]*)\b[^>]*>/g)) {
    const name = tag[2].toLowerCase()
    if (tag[1]) assert.equal(stack.pop(), name, `unexpected closing ${name}`)
    else if (!voids.has(name) && !tag[0].endsWith('/>')) stack.push(name)
  }
  assert.deepEqual(stack, [], 'unclosed tags')
})
check('numeric SVG geometry and local marker references', () => {
  for (const tag of tags.filter(tag => ['rect', 'text', 'marker'].includes(tag[1]))) {
    for (const attr of tag[2].matchAll(/\b(x|y|width|height|rx|markerWidth|markerHeight|refX|refY)="([^"]+)"/g)) {
      assert.match(attr[2], /^-?\d+(?:\.\d+)?$/, `invalid SVG ${attr[1]}=${attr[2]}`)
    }
  }
  for (const match of html.matchAll(/url\(#([^)]+)\)/g)) assert.ok(idSet.has(match[1]))
})
check('simple CSS dimension keywords', () => {
  const keywords = new Set(['auto', 'none', 'inherit', 'initial', 'unset', 'revert', 'min-content', 'max-content', 'fit-content'])
  for (const match of css.matchAll(/(?:min-|max-)?(?:width|height):\s*([a-z-]+)\s*[;}]/g)) {
    assert.ok(keywords.has(match[1]), `invalid dimension keyword: ${match[1]}`)
  }
})
check('offline artifact without network code or runtime dependencies', () => {
  for (const tag of tags) assert.doesNotMatch(tag[2], /\b(?:src|srcset)=/, 'external runtime asset')
  assert.equal((html.match(/<script\b/g) || []).length, 1)
  assert.doesNotMatch(css, /@import|url\((?!#)/)
  assert.doesNotMatch(script, /\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|eval)\s*\(/)
  assert.doesNotMatch(html, /@@[A-Z_]+@@/, 'unexpanded build token')
})
check('12 full chapter targets, preserved sample, glossary and all declared sources', () => {
  const count = prefix => ids.filter(id => id.startsWith(prefix)).length
  assert.equal(count('chapter-'), 12)
  const chapterNames = ['business', 'entry', 'profile', 'sources', 'rag', 'agents', 'risk', 'persistence', 'review', 'sse', 'run', 'tests']
  for (const name of chapterNames) assert.ok(idSet.has(`body-${name}`), `missing chapter body: ${name}`)
  assert.equal(count('step-'), 7)
  assert.equal(count('struct-'), 5)
  assert.equal(count('term-'), 34)
  const fragmentNames = ['01-02-business-entry', '04-05-sources-rag', '06-07-agents-risk', '08-09-storage-review', '10-12-interface-runtime-tests']
  let sourceCount = 37 // The original sample's complete set is also checked below.
  for (const name of fragmentNames) {
    const refs = JSON.parse(readFileSync(path.join(folder, 'chapters', `${name}.refs.json`), 'utf8'))
    sourceCount += refs.length
    for (const [id] of refs) assert.ok(idSet.has(id), `missing declared source ${id}`)
  }
  assert.equal(tags.filter(tag => tag[1] === 'details' && /class="source"/.test(tag[2])).length, sourceCount)
})
check('every link target from the delivered sample remains available', () => {
  const previous = execFileSync('git', ['show', '7b4c66e:docs/project-handbook/index.html'], { cwd: path.resolve(folder, '../..'), encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 })
  const allCurrentIds = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]))
  for (const match of previous.matchAll(/\bid="([^"]+)"/g)) assert.ok(allCurrentIds.has(match[1]), `removed sample target ${match[1]}`)
})
check('script targets exist and inline JavaScript parses', () => {
  for (const match of script.matchAll(/byId\('([^']+)'\)/g)) assert.ok(idSet.has(match[1]), `missing JS target ${match[1]}`)
  new vm.Script(script, { filename: 'handbook-script.js' })
})
const context = vm.createContext({ module: { exports: {} } })
vm.runInContext(script, context)
const logic = context.module.exports
check('search: Chinese, case folding, multiple terms, blank and no match', () => {
  const entries = [{ id: 'a', text: '档案 coverage 来源' }, { id: 'b', text: '研究快照 HMAC binding' }]
  assert.equal(logic.search(entries, '来源')[0].id, 'a')
  assert.equal(logic.search(entries, '  hMaC  binding ')[0].id, 'b')
  assert.equal(logic.search(entries, '档案 来源').length, 1)
  assert.equal(logic.search(entries, '档案 binding').length, 0)
  assert.equal(logic.search(entries, '   ').length, 0)
  assert.equal(logic.search(entries, '不存在的词').length, 0)
})
check('version illustration: frozen input unchanged across update and resume', () => {
  const first = logic.revisionStep(0), update = logic.revisionStep(1), resume = logic.revisionStep(2)
  assert.notEqual(first.current, update.current)
  assert.equal(first.frozen, update.frozen)
  assert.equal(first.frozen, resume.frozen)
  assert.equal(resume.current, update.current)
  assert.match(resume.note, /校验/)
  for (const value of [-1, 3, NaN, 0.5, '1']) assert.equal(logic.revisionStep(value).step, 0)
})
check('JSON examples parse and source example covers both derived fields', () => {
  const block = id => html.match(new RegExp(`<details id="${id}"[\\s\\S]*?<pre><code>([\\s\\S]*?)<\\/code><\\/pre>`))[1]
  const minimal = JSON.parse(block('example-minimal'))
  const sourced = JSON.parse(block('example-source'))
  assert.ok(minimal.profile.name.trim())
  assert.deepEqual(sourced.field_sources[0].field_ids, ['registration', 'operating_status'])
  assert.equal(sourced.profile.registration.operating_status, '存续')
})
check('committed source snapshots and generated artifact reproduce exactly', () => {
  console.log(execFileSync(process.execPath, [path.join(folder, 'build.mjs'), '--check'], { encoding: 'utf8' }).trim())
})
console.log(`\n${checks} checks passed. Browser layout and real DOM interaction were not tested.`)
