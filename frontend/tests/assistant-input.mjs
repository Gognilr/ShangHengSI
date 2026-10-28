import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

// 直接执行组件的入参构造，按后端既定模板契约检查，不在测试中复制构造逻辑。
const source = readFileSync(new URL('../src/components/assistant/Composer.vue', import.meta.url), 'utf8')
const body = source.match(/function input\(\): MessageInput \{([\s\S]*?)\n\}/)?.[1]
assert.ok(body)
const compiled = ts.transpile(`function input() {${body}\n}`)
const build = new Function('props', 'fileIds', 'target', 'focus', 'tone', 'language', 'text', 'useKb', 'useHistory', `${compiled}; return input()`)
const make = (template, enabled = true) => build(
  { template, session: { kb_enabled: enabled } }, { value: ['file-one'] }, { value: '英文' },
  { value: '全部' }, { value: '正式' }, { value: '同来函' }, { value: '虚构来函' },
  { value: true }, { value: false },
)
test('翻译按单文件契约提交 file_id 与 target', () => {
  assert.deepEqual(make('translate').params, { file_id: 'file-one', target: '英文' })
})
test('其余模板保持 file_ids 与各自选项', () => {
  assert.deepEqual(make('summary').params, { file_ids: ['file-one'], focus: '全部' })
  assert.deepEqual(make('reply').params, { file_ids: ['file-one'], tone: '正式', language: '同来函' })
  for (const template of ['crosscheck', 'compare', null]) {
    assert.deepEqual(make(template).params, { file_ids: ['file-one'] })
  }
})
test('保留来函与参考选择，总开关关闭时不请求知识库', () => {
  assert.equal(make('translate').message, '虚构来函')
  assert.equal(make('translate').use_history, false)
  assert.equal(make('translate').use_kb, true)
  assert.equal(make('translate', false).use_kb, false)
})
