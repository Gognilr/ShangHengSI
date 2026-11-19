// 受邀单位名单与投标人分组的名称比对（V34-B-01b）。服务器按 normalize_value 再判一次同名，这里只用于界面提示与预填。
const SUFFIX = /(?:集团)?(?:股份有限公司|有限责任公司|有限公司|股份公司|公司|厂)$/
const REGION = /^[一-鿿]{2,3}(?:省|市|自治区)/

export function nameKey(name: string): string {
  return name.replace(/\s+/g, '').normalize('NFKC').toLowerCase()
}

/** 全称、去后缀、再去省市前缀（4 字以上），与后端 checks.short_names 一致 */
export function shortNames(name: string): string[] {
  const full = name.replace(/\s+/g, ''), core = full.replace(SUFFIX, '')
  return [...new Set([full, core, core.replace(REGION, '')].filter(n => n.length >= 4))]
}

/** 文件夹名或已有分组名能唯一对上的名单全称；对不上或对上多家时 null */
export function matchInvitee(raw: string, invitees: string[]): string | null {
  // 文件夹名常带序号与“投标文件”等字样：01-某某机电投标文件
  const text = raw.replace(/^[\d\s._\-—、()（）[\]【】]+/, '').replace(/[\s_\-—（(]*(?:的)?(?:投标文件|投标书|标书|响应文件|商务标|技术标|报价文件)[)）]?$/, '')
  const k = nameKey(text)
  if (!k) return null
  const exact = invitees.find(n => nameKey(n) === k)
  if (exact) return exact
  const hits = invitees.filter(n => shortNames(n).some(s => k.includes(nameKey(s))) || shortNames(text).some(s => s.length >= 4 && nameKey(n).includes(nameKey(s))))
  return hits.length === 1 ? hits[0]! : null
}
