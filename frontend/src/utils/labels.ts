/** 枚举翻译（SPEC 19.1：接口返回数据库代码，由前端翻译为中文）。未知代码原样显示。 */

type Dict = Record<string, string>

function pick(dict: Dict) {
  return (code: string | null | undefined): string => (code ? (dict[code] ?? code) : '—')
}

export const STATUS_OPTIONS: Dict = {
  parsing: '解析中',
  parse_failed: '解析失败',
  awaiting_confirm: '待确认脱敏',
  reviewing: '审核中',
  reviewed: '已审核',
  completed_local: '本地校验完成',
}
export const statusLabel = pick(STATUS_OPTIONS)

export const RUN_STATUS: Dict = {
  queued: '排队中',
  running: '审核中',
  blocked: '已被拦截',
  completed: '已完成',
  partial: '部分完成',
  failed: '失败',
}
export const runStatusLabel = pick(RUN_STATUS)

export const STANCE_OPTIONS: Dict = { sales: '销售', purchase: '采购', other: '其他' }
export const stanceLabel = pick(STANCE_OPTIONS)

export const SOURCE_OPTIONS: Dict = { outgoing: '我方发出', incoming: '对方发来' }
export const sourceLabel = pick(SOURCE_OPTIONS)

export const POLICY_OPTIONS: Dict = { ai: '脱敏后 AI 审核', local_only: '不外发，仅本地校验' }
export const policyLabel = pick(POLICY_OPTIONS)

export const DOC_TYPE_OPTIONS: Dict = {
  contract: '合同',
  quotation: '报价单',
  order: '订单/供货单',
  inquiry: '询价函',
  letter: '函件',
}
export const docTypeLabel = pick(DOC_TYPE_OPTIONS)

export const DOC_TYPE_SOURCE: Dict = { auto: '自动识别', user: '手动修改' }
export const docTypeSourceLabel = pick(DOC_TYPE_SOURCE)

export const CATEGORY_OPTIONS: Dict = {
  wording: '措辞',
  logic: '逻辑',
  legal: '法律',
  compliance: '合规',
  commercial: '商务',
}
export const categoryLabel = pick(CATEGORY_OPTIONS)

export const SEVERITY_OPTIONS: Dict = { high: '高', medium: '中', low: '低' }
export const severityLabel = pick(SEVERITY_OPTIONS)
/** 严重程度排序用：高 → 低。 */
export const SEVERITY_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 }

export const VERDICT_OPTIONS: Dict = { issue: '有问题', ok: '未发现问题', na: '不适用', unable: '无法判断' }
export const verdictLabel = pick(VERDICT_OPTIONS)

export const CITE_STATUS: Dict = { verified: '引用已校验', unverified: '待核实', missing: '缺失型' }
export const citeStatusLabel = pick(CITE_STATUS)

export const HANDLING_OPTIONS: Dict = { pending: '待处理', adopted: '已采纳', ignored: '已忽略' }
export const handlingLabel = pick(HANDLING_OPTIONS)

export const REQ_SOURCE: Dict = { builtin: '内置要求', company: '公司补充要求', note: '本次补充说明' }
export const reqSourceLabel = pick(REQ_SOURCE)

/** 制裁结论三档（SPEC 6.3）。 */
export const SANCTIONS_LEVEL: Dict = { none: '无明显风险', attention: '需注意', high: '高风险（建议合规人员或律师确认）' }
export const sanctionsLabel = pick(SANCTIONS_LEVEL)

/** 脱敏类别（SPEC 9.2），用于脱敏摘要。 */
export const REDACTION_CATEGORY: Dict = {
  OUR: '我方主体',
  ORG: '名称',
  PERSON: '人名',
  PHONE: '电话',
  EMAIL: '邮箱',
  URL: '网址',
  ADDR: '地址',
  ACCOUNT: '账号',
  SWIFT: 'SWIFT 代码',
  IDNO: '证件号',
  REFNO: '编号',
  AMOUNT: '金额',
  PRICE: '单价',
  QTY: '数量',
  CUSTOM: '敏感词',
}
export const redactionCategoryLabel = pick(REDACTION_CATEGORY)

/** 把 options 对象转成下拉框用的数组。 */
export function toOptions(dict: Dict): { value: string; label: string }[] {
  return Object.entries(dict).map(([value, label]) => ({ value, label }))
}

/** 标签颜色（frontend.md 第五节）：各页面共用。 */
export type TagType = 'danger' | 'warning' | 'info' | 'success'
export const SEVERITY_TAG: Record<string, TagType> = { high: 'danger', medium: 'warning', low: 'info' }
export const VERDICT_TAG: Record<string, TagType> = { issue: 'danger', ok: 'success', na: 'info', unable: 'warning' }
export const CITE_TAG: Record<string, TagType> = { verified: 'success', unverified: 'warning', missing: 'info' }
