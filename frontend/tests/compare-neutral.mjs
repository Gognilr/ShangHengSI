import assert from 'node:assert/strict'
import test from 'node:test'
import { buildCompareRows } from '../src/utils/compare.ts'

const node = (loc, text) => ({ loc, text, kind: 'paragraph', part: 'body' })
const text = segments => segments.map(segment => segment.text).join('')
const item = { loc: 'F1-P1', char_start: 2, char_end: 4, placeholder: '[[ORG_1]]', category: 'ORG' }

test('助手首条正文与原文配对，不生成主体说明或不发送行', () => {
  const rows = buildCompareRows('⟦F1-P1⟧😀 公司  ', [node('F1-P1', '😀 公司  ')], [], [])
  assert.equal(rows.length, 1)
  assert.equal(rows[0].kind, 'pair')
  assert.equal(rows[0].loc, 'F1-P1')
  assert.equal(text(rows[0].right), '😀 公司  ')
  assert.equal(text(rows[0].left[0]), '😀 公司  ')
})
test('两种格式均按位置高亮，保留正文前后空格及 Unicode 字符', () => {
  for (const prefix of ['[F1-P1] ', '⟦F1-P1⟧']) {
    const rows = buildCompareRows(prefix + '😀 [[ORG_1]]  ', [node('F1-P1', '😀 公司  ')], [item], [])
    assert.equal(rows.length, 1)
    assert.equal(rows[0].kind, 'pair')
    assert.equal(rows[0].hasRedaction, true)
    assert.equal(text(rows[0].right), '😀 [[ORG_1]]  ')
    assert.equal(text(rows[0].left[0]), '😀 公司  ')
    assert.equal(rows[0].left[0].find(segment => segment.placeholder)?.text, '公司')
  }
})
test('审核的主体说明与旧位置格式保持原行为', () => {
  const rows = buildCompareRows('系统签约方说明\n[F1-P1]  正文  ', [node('F1-P1', ' 正文  ')], [], [])
  assert.deepEqual(rows.map(row => row.kind), ['header', 'pair'])
  assert.equal(text(rows[0].right), '系统签约方说明')
  assert.equal(text(rows[1].right), ' 正文  ')
})
test('助手正文前导空格不会被前缀解析吃掉', () => {
  const rows = buildCompareRows('⟦F1-P1⟧  \t正文  ', [node('F1-P1', '  \t正文  ')], [], [])
  assert.equal(text(rows[0].right), '  \t正文  ')
})
test('未知位置仍标未对齐，未发送节点仍单独列出', () => {
  const rows = buildCompareRows('⟦F9-P1⟧未知位置', [node('F1-P1', '原文')], [], [])
  assert.deepEqual(rows.map(row => row.kind), ['unmatched', 'not_sent'])
  assert.equal(text(rows[0].right), '未知位置')
  assert.equal(rows[1].right, null)
})
test('逐格位置不会误聚合成整表，正文字符完整保留', () => {
  const rows = buildCompareRows('⟦F1-T1-R1-C1⟧项目\n⟦F1-T1-R1-C2⟧  数量',
    [node('F1-T1-R1-C1', '项目'), node('F1-T1-R1-C2', '  数量')], [], [])
  assert.deepEqual(rows.map(row => row.kind), ['pair', 'pair'])
  assert.deepEqual(rows.map(row => text(row.right)), ['项目', '  数量'])
})
