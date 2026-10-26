/** 枚举翻译（SPEC 19.1：接口返回数据库代码，由前端翻译为中文）。未知代码原样显示。 */

type Dict = Record<string, string>

export const LAW_STATUS: Dict = { not_yet: '尚未生效', valid: '有效', amended: '已修改', repealed: '已废止' }
export const LAW_IMPORT_STATUS: Dict = { parsing: '解析中', pending: '待确认', applied: '已生效', discarded: '已放弃', failed: '失败' }
export const LAW_IMPORT_KIND: Dict = { catalog: '目录导入', fulltext: '全文导入', sync: '自动同步' }
export const LAW_SOURCE: Dict = { builtin: '内置', import: '手动导入', sync: '自动同步' }
export const LAW_FIELD: Dict = { status: '时效性', category: '分类', effective_on: '施行日期', office: '制定机关', flk_id: '官方编号' }
export const TEMPLATE_STATE: Dict = { current: '', modified: '（范本已修改）', deleted: '（范本已删除）' }
export const LAWSYNC_INTERVAL: Dict = { monthly: '每月', weekly: '每周' }

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
  wording: '表述',
  logic: '逻辑',
  legal: '法律',
  compliance: '合规与制裁',
  commercial: '商务',
}
export const categoryLabel = pick(CATEGORY_OPTIONS)

export const SEVERITY_OPTIONS: Dict = { high: '高', medium: '中', low: '低' }
export const severityLabel = pick(SEVERITY_OPTIONS)
export const LOCAL_CHECK: Dict = {
  'L-12': '公司范本缺失条款',
  'L-01': '行金额', 'L-02': '合计', 'L-03': '税额', 'L-04': '大小写一致',
  'L-05': '跨文件金额', 'L-06': '币种一致', 'L-07': '报价有效期',
  'L-08': '交期与签约日', 'L-INFO': '提示',
  'L-09': '分期金额', 'L-10': '废止法律', 'L-11': '增值税税率',
}
export const localCheckLabel = pick(LOCAL_CHECK)
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
  POSTCODE: '邮编',
  ACCOUNT: '账号',
  SWIFT: 'SWIFT / 银行代码',
  IM: '即时通信账号',
  PROJECT: '项目名称',
  IDNO: '证件号',
  REFNO: '编号',
  AMOUNT: '金额',
  PRICE: '单价',
  QTY: '数量',
  CUSTOM: '敏感词',
}
export const redactionCategoryLabel = pick(REDACTION_CATEGORY)

/** 疑似遗漏的原因（设计 M4 第六节）。 */
export const SUSPECT_REASON: Dict = {
  derived_name: '名称简写', person_like: '疑似人名', ner: '模型识别',
  person_name: '疑似人名',
  org_suffix: '带企业后缀的名称',
  capitalized: '连续大写的英文单词',
  long_digits: '长串数字',
  before_company: '“公司”“厂”前的文字',
}
export const suspectReasonLabel = pick(SUSPECT_REASON)

/** 追问可选的替换类别，保持既有接口的三类。 */
export const USER_CATEGORY_OPTIONS: Dict = { ORG: '公司', PERSON: '人名', CUSTOM: '其他' }
/** 确认脱敏页十类；追问仍使用上面的三类，避免扩大追问接口契约。 */
export const MANUAL_CATEGORY_OPTIONS: Dict = {
  ORG: '公司', PERSON: '人名', IM: '联系方式', ACCOUNT: '账号', IDNO: '证件号',
  REFNO: '编号', ADDR: '地址', AMOUNT: '金额', PROJECT: '项目', CUSTOM: '其他',
}

/** 账号角色、状态（设置 - 账号）。 */
export const ROLE_OPTIONS: Dict = { user: '普通用户', sys_admin: '系统管理员', biz_admin: '业务管理员', admin: '系统与业务管理员' }
export const roleLabel = pick(ROLE_OPTIONS)
export const USER_STATUS: Dict = { active: '启用中', disabled: '已停用' }
export const userStatusLabel = pick(USER_STATUS)

/** 把 options 对象转成下拉框用的数组。 */
export function toOptions(dict: Dict): { value: string; label: string }[] {
  return Object.entries(dict).map(([value, label]) => ({ value, label }))
}

/** 标签颜色（frontend.md 第五节）：各页面共用。 */
export type TagType = 'danger' | 'warning' | 'info' | 'success'
export const RUN_STATUS_TAG: Record<string, TagType> = {
  completed: 'success', partial: 'warning', blocked: 'warning', failed: 'danger', queued: 'info', running: 'info',
}
export const SEVERITY_TAG: Record<string, TagType> = { high: 'danger', medium: 'warning', low: 'info' }
export const VERDICT_TAG: Record<string, TagType> = { issue: 'danger', ok: 'success', na: 'info', unable: 'warning' }
export const CITE_TAG: Record<string, TagType> = { verified: 'success', unverified: 'warning', missing: 'info' }

/** 备份、外发记录（设置 - 数据）。 */
export const BACKUP_STATUS: Dict = { running: '进行中', success: '成功', failed: '失败' }
export const backupStatusLabel = pick(BACKUP_STATUS)
export const BACKUP_STATUS_TAG: Record<string, TagType> = { running: 'info', success: 'success', failed: 'danger' }
export const BACKUP_TRIGGER: Dict = { schedule: '定时', manual: '手动' }
export const backupTriggerLabel = pick(BACKUP_TRIGGER)
export const OUTBOUND_STATUS: Dict = { sent: '已发送', blocked: '已拦截', error: '出错' }
export const outboundStatusLabel = pick(OUTBOUND_STATUS)
export const OUTBOUND_STATUS_TAG: Record<string, TagType> = { sent: 'success', blocked: 'warning', error: 'danger' }
export const LLM_PURPOSE: Dict = { review: '审核', chat: '追问', test: '连接测试' }
export const llmPurposeLabel = pick(LLM_PURPOSE)

/** 审计动作名称；未知动作按通用翻译规则保留原值。 */
export const AUDIT_ACTION: Dict = {
  login: '登录', login_failed: '登录失败', password_change: '修改密码',
  user_create: '创建账号', user_update: '修改账号',
  setting_update: '修改设置', task_create: '创建审核任务', task_delete: '删除审核任务',
  run_create: '确认发送', backup_run: '执行备份', retention_run: '执行保留期清理',
  redaction_revoke: '撤销替换', report_export: '导出报告', restore_run: '恢复备份',
}
export const auditActionLabel = pick(AUDIT_ACTION)
export const AUDIT_TARGET: Dict = { user: '账号', task: '任务', run: '审核轮次', finding: '问题', setting: '设置', term: '敏感词', backup: '备份', company_requirement: '审核要求' }
export const auditTargetLabel = pick(AUDIT_TARGET)
/** 对照视图与手动补充共用的类别色组。 */
export const REDACTION_KIND: Record<string, string> = {
  IM: 'contact', PROJECT: 'other',
  OUR: 'org', ORG: 'org', PERSON: 'person', PHONE: 'contact', EMAIL: 'contact', URL: 'contact',
  ADDR: 'addr', POSTCODE: 'addr', ACCOUNT: 'id', SWIFT: 'id', IDNO: 'id', REFNO: 'id',
  AMOUNT: 'amount', PRICE: 'amount', QTY: 'amount', CUSTOM: 'other',
}
