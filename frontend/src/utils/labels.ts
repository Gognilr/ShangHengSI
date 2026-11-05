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
  draft: '草稿',
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
  'L-13': '报表勾稽', 'L-14': '分级合计', 'L-15': '公式重算', 'L-16': '疑似手填',
  'L-17': '收款账号不一致', 'L-18': '签约方名称不一致', 'L-19': '编号、单价、数量不一致',
  'L-20': '隐藏文字', 'L-21': '疑似写给 AI 的指令',
}
export const localCheckLabel = pick(LOCAL_CHECK)

/** “未能还原”标记的提示（V31-U3 0.4）。 */
export const MARK_KIND: Dict = {
  variant: '代号写法有变化，无法确定指哪个对象，未还原',
  unknown: '模型提到了文件中没有的对象',
  shared: '共用代号，此处可能对应多个值，未还原',
  note: '系统标记，不是合同文字',
  date: '模型写的日期，未能对应到原文日期，请核对',
  literal: '模型自己写的账号、金额等，不是来自文件，请核对',
  calc: '算式未能在本机计算',
}
export const markKindLabel = pick(MARK_KIND)

/** 无法判断的原因（SPEC 11.6）。 */
export const UNABLE_REASON: Dict = { redacted: '信息已脱敏', absent: '文件未写', other: '其他' }
export const unableReasonLabel = pick(UNABLE_REASON)

/** 本机计算未计算的原因（SPEC 13）。 */
export const CALC_STATUS: Dict = {
  unknown_code: '算式含文件中没有的代号，未计算',
  unparsable: '算式无法解析，未计算',
  currency_mixed: '算式含两种币种，未计算',
  div_zero: '除数为 0，未计算',
}
export const calcStatusLabel = pick(CALC_STATUS)

/** L-21 命中时结果页的固定提醒，与报告首页同一句（V31Q-67 ①，后端 review/coverage.ai_instruction_notice）。 */
export function aiInstructionNotice(count: number): string {
  return `文件含疑似写给 AI 的指令（${count} 处），结论可能受其影响，请人工核对相关条款`
}

/** 外发回执中“不发送”的各项（SPEC 18.29）。 */
export const NOT_SENT: Dict = {
  images: '图片与嵌入对象',
  comments: '批注',
  headers_footers: '页眉页脚',
  hidden_sheets: '隐藏工作表',
}
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
  NAME: '名称（不确定）',
  TERM: '专有名词（不确定）',
}
export const redactionCategoryLabel = pick(REDACTION_CATEGORY)

/** 不确定项为何保留原文（V31-S 视图的 kept_by）。 */
export const KEPT_BY: Dict = { user: '你保留的', memory: '按术语记忆保留', vector: '本机判断为普通术语' }
export const keptByLabel = pick(KEPT_BY)

/** 不确定项（原“疑似遗漏”）的原因（设计 M4 第六节）。 */
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
export type TagType = 'danger' | 'warning' | 'info' | 'success' | 'primary'
export const RUN_STATUS_TAG: Record<string, TagType> = {
  completed: 'success', partial: 'warning', blocked: 'warning', failed: 'danger', queued: 'info', running: 'info',
}
export const SEVERITY_TAG: Record<string, TagType> = { high: 'danger', medium: 'warning', low: 'info' }
export const VERDICT_TAG: Record<string, TagType> = { issue: 'danger', ok: 'success', na: 'info', unable: 'warning' }
export const CITE_TAG: Record<string, TagType> = { verified: 'success', unverified: 'warning', missing: 'info' }

/** 任务 / 文件处理状态标签（V3-A 4.2）：解析中 info、待确认 warning、已确认 success、失败 danger；只读、已过期为 info + plain。
 *  审核完成用 primary 而不用 success：绿色的“已审核”容易被读成“审核通过”（frontend.md 第五节）。 */
const STATUS_TAG: Record<string, [TagType, 'light' | 'plain']> = {
  draft: ['info', 'light'], parsing: ['info', 'light'], reviewing: ['info', 'light'], queued: ['info', 'light'], streaming: ['info', 'light'],
  awaiting_confirm: ['warning', 'light'],
  reviewed: ['primary', 'light'], completed_local: ['primary', 'light'], confirmed: ['success', 'light'], done: ['success', 'light'],
  parse_failed: ['danger', 'light'], failed: ['danger', 'light'],
  readonly: ['info', 'plain'], expired: ['info', 'plain'],
}
export function statusTag(code: string): { type: TagType; effect: 'light' | 'plain' } {
  const [type, effect] = STATUS_TAG[code] ?? ['info', 'plain']
  return { type, effect }
}

/** 备份、外发记录（设置 - 数据）。 */
export const BACKUP_STATUS: Dict = { running: '进行中', success: '成功', failed: '失败' }
export const backupStatusLabel = pick(BACKUP_STATUS)
export const BACKUP_STATUS_TAG: Record<string, TagType> = { running: 'info', success: 'success', failed: 'danger' }
export const BACKUP_TRIGGER: Dict = { schedule: '定时', manual: '手动' }
export const backupTriggerLabel = pick(BACKUP_TRIGGER)
export const OUTBOUND_STATUS: Dict = { sent: '已发送', blocked: '已拦截', error: '出错' }
export const outboundStatusLabel = pick(OUTBOUND_STATUS)
export const OUTBOUND_STATUS_TAG: Record<string, TagType> = { sent: 'success', blocked: 'warning', error: 'danger' }
export const LLM_PURPOSE: Dict = { review: '审核', chat: '追问', test: '连接测试', assist: '智能助手' }
export const llmPurposeLabel = pick(LLM_PURPOSE)

export const ASSISTANT_TEMPLATE: Dict = { translate: '翻译', summary: '摘要', reply: '起草回复', crosscheck: '单证核对', compare: '比价', minutes: '会议纪要' }
export const ASSISTANT_STATUS: Dict = { active: '可继续', parsing: '解析中', parse_failed: '解析失败', awaiting_confirm: '待确认', queued: '排队中', streaming: '生成中', done: '已完成', stopped: '已停止', failed: '失败' }
export const assistantStatusLabel = pick(ASSISTANT_STATUS)
export const KB_STATUS: Dict = { parsing: '解析中', ready: '可用', failed: '解析失败' }
export const KB_STATUS_TAG: Record<string, TagType> = { parsing: 'info', ready: 'success', failed: 'danger' }
export const ASSISTANT_ERROR: Dict = { SESSION_READONLY: '原件已按保留期限删除，本会话只能查看', ASSIST_INTERRUPTED: '服务重启，回复中断，请重试', ASSIST_TOO_LONG: '所选文件过长，请减少使用的文件', ASSIST_QUOTA: '翻译调用次数不足，请明天再试或联系管理员' }

/** 审计动作名称；未知动作按通用翻译规则保留原值。 */
export const AUDIT_ACTION: Dict = {
  assistant_session_create: '创建会话', assistant_session_delete: '删除会话', assistant_files_confirm: '确认会话脱敏',
  assistant_export: '导出会话回复', kb_upload: '上传知识库文档', kb_update: '修改知识库文档', kb_delete: '删除知识库文档',
  login: '登录', login_failed: '登录失败', password_change: '修改密码',
  user_create: '创建账号', user_update: '修改账号',
  setting_update: '修改设置', task_create: '创建审核任务', task_delete: '删除审核任务',
  run_create: '确认发送', backup_run: '执行备份', retention_run: '执行保留期清理',
  redaction_revoke: '撤销替换', redaction_keep: '确认可发送', report_export: '导出报告', restore_run: '恢复备份',
  law_import_upload: '上传法规导入文件', law_import_apply: '确认法规更新',
  law_import_discard: '放弃法规更新', law_sync_run: '执行法规同步',
  law_update: '修改法规信息',
  template_create: '创建公司范本', template_update: '修改公司范本',
  template_delete: '删除公司范本', template_clause_update: '修改范本条款',
}
export const auditActionLabel = pick(AUDIT_ACTION)
export const AUDIT_TARGET: Dict = { user: '账号', task: '任务', run: '审核轮次', finding: '问题', setting: '设置', term: '敏感词', backup: '备份', company_requirement: '审核要求', law_import: '法规更新', law: '法规', template: '公司范本', kb_doc: '知识库文档' }
export const auditTargetLabel = pick(AUDIT_TARGET)
/** 对照视图与手动补充共用的类别色组。 */
export const REDACTION_KIND: Record<string, string> = {
  IM: 'contact', PROJECT: 'other',
  OUR: 'org', ORG: 'org', PERSON: 'person', PHONE: 'contact', EMAIL: 'contact', URL: 'contact',
  ADDR: 'addr', POSTCODE: 'addr', ACCOUNT: 'id', SWIFT: 'id', IDNO: 'id', REFNO: 'id',
  AMOUNT: 'amount', PRICE: 'amount', QTY: 'amount', CUSTOM: 'other',
  // 不确定项的默认替换：类别色同“其他”，画布按 detected_by=uncertain 另画虚线胶囊（V31-U2 4.2）
  NAME: 'other', TERM: 'other',
}
