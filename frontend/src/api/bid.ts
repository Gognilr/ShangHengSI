import { http } from './http'

export type BidStage = 'preparing' | 'ready' | 'checking' | 'checked'
export interface BidSummary { id: string; title: string; stage: BidStage; status: string; opening_date: string; last_activity_at: string; category_id: number | null; folder_id: string | null }
export interface BidFolder { id: string; parent_id: string | null; name: string; count: number }
export interface BidFolderTree { folders: BidFolder[]; all: number; unfiled: number }
export interface BidCategory { id: number; name: string; enabled: boolean; used?: number }
/** 列表筛选（V34-B-01a）：folder 为 none 表示未归档 */
export interface BidListQuery { page?: number; q?: string; category?: number; folder?: string; stage?: BidStage; from?: string; to?: string }
export interface BidFile {
  id: string; bidder_id: string | null; name: string; ext: string; size_bytes: number; page_count: number | null
  has_password: boolean; needs_password: boolean; state: 'failed' | 'done' | 'waiting'; error: string | null; warnings: string[]; original_deleted: boolean
}
/** 唱标价（V34-B-06e）：人工录入的小写金额、大写与备注 */
export interface BidOpeningPrice { amount: string; upper: string | null; note: string | null }
export interface BidOpeningPriceInput { amount: string | null; upper?: string | null; note?: string | null }
export interface Bidder { id: string; name: string; code: string; seq: number; opening_price?: (BidOpeningPrice & { text: string }) | null }
export interface BidProject extends BidSummary {
  policy: 'ai' | 'local_only'; busy: boolean; error_message: string | null; bidders: Bidder[]; files: BidFile[]
  limits: { bid_files: number; tender_files: number; file_mb: number; pdf_pages: number; ocr_pages: number }
  requirements: { enabled: number; extract_state: BidExtractState }
  check: { state: null | 'running' | 'done' | 'failed'; error: string | null; ocr: { done: number; total: number } }
  model: { state: BidModelState; error: string | null; done: number; total: number }
  sent_count?: number
}
/** 模型核对（V34-B-05）：片段脱敏 → 待确认 → 发送中 → 完成 / 失败 */
export type BidModelState = null | 'preparing' | 'awaiting' | 'running' | 'done' | 'failed'
export interface BidSnippet { id: string; file: string; page: number | null; text: string; ai_text: string; requirements: string[] }
export interface BidModelView {
  state: BidModelState; error: string | null; progress: { done: number; total: number }
  second?: boolean; second_available?: boolean; similar_preview?: string[]
  receipt: { snippets: number; chars: number; protected: number; calls: number; total_calls: number; second_calls?: number; remaining: { monthly: number | null; daily: number | null }; notes: string[] } | null
  bidders: { id: string; name: string; code: string; snippets: BidSnippet[] }[]
  requirements: { id: string; n: string; group: BidGroup; text: string; ai_text: string }[]
  sent_count?: number
}
export type BidGroup = 'qualification' | 'substantive' | 'veto' | 'pricing' | 'validity' | 'signing' | 'format'
export type BidMethod = 'local' | 'model' | 'manual'
export type BidExtractState = null | 'redacting' | 'awaiting' | 'extracting' | 'done' | 'failed'
export interface BidLoc { file_id: string; loc: string; start: number; end: number }
export interface BidRequirement { id: string; group: BidGroup; text: string; locs: BidLoc[]; method: BidMethod; enabled: boolean; source: 'marked' | 'model' | 'default' | 'manual'; seq: number; file: string | null }
export interface BidParameters { ceiling: string | null; currency: 'CNY' | 'USD' | 'EUR' | 'HKD'; validity_days: number | null; deposit: string | null; deposit_forms: string; consortium: boolean | null; tax_included: boolean | null; agency?: string }
export interface BidCount { total: number; covered: number; missing: (BidLoc & { text: string })[] }
export interface BidRequirements {
  parameters: BidParameters; parameter_sources: Record<string, { file_id: string; loc: string }>; parameters_confirmed: boolean
  extracted: boolean; tender_changed: boolean; items: BidRequirement[]; counts: { star: BidCount; veto: BidCount }; version: number
  extract_state: BidExtractState; extract_error: string | null; stage: BidStage
  receipt: { chars: number; calls: number; protected: number; remaining: { monthly: number | null; daily: number | null } } | null
  sent_count?: number
}
export interface BidTender { files: Record<string, string>; nodes: { file_id: string; loc: string; kind: string; text: string }[]; markers: { kind: 'star' | 'veto'; loc: string; start: number; end: number }[] }
export const bidGroupLabel: Record<BidGroup, string> = {
  qualification: '资格条件', substantive: '实质性条款（★）', veto: '否决条款', pricing: '报价要求', validity: '有效期与保证金', signing: '签章与格式页', format: '投标文件格式（范本）',
}
export const bidMethodLabel: Record<BidMethod, string> = { local: '本地规则', model: '模型判断', manual: '人工核对' }
export const bidSourceLabel: Record<BidRequirement['source'], string> = { marked: '原文标记', model: '模型提取 · 请核对', default: '默认（第五十一条）', manual: '手工' }
export type BidLevel = 'strong' | 'medium' | 'weak' | 'normal' | 'veto' | 'general' | 'hint'
export interface BidPlace { file_id: string; loc?: string; page: number | null; excerpt?: string; name?: string }
export interface BidEvidenceRow {
  label: string; value?: string; count?: number; sha256?: string; width?: number; height?: number
  files?: Record<string, string>; places?: Record<string, BidPlace[]>
  /** 相似段落：两家原文并列 */
  chars?: number; sentences?: number; section?: string; level?: BidLevel
  left?: { bidder_id: string; places: BidPlace[]; text: string }; right?: { bidder_id: string; places: BidPlace[]; text: string }
  /** 报价规律：对齐的分项 */
  items?: { name: string; left: string; right: string }[]
}
export interface BidItem extends BidReview {
  id: string; kind: 'issue' | 'clue' | 'normal_dup'; type: string; level: BidLevel; article: string | null; bidder_ids: string[]
  source: 'local' | 'model'; title: string; rows: BidEvidenceRow[]; normal?: string; reference_reason?: string
  requirement_id: string | null
  /** 相似段落位置条（V34-B-06d）：两家按句序的轴，文件依次排列；a / b 为句序范围，pa / pb 为页码范围 */
  map?: BidSimilarMap
}
export interface BidSimilarMap {
  n: Record<string, number>; files: Record<string, [string, number][]>
  segs: { level: BidLevel; chars: number; a: [number, number]; b: [number, number]; pa: [number, number] | null; pb: [number, number] | null; fa: string; fb: string }[]
}
/** 文件时间线（甘特图）：bidder_id 为空是招标文件；group 相同的文件来自不同投标人且属性相关 */
export interface BidTimelineFile { bidder_id: string | null; file_id: string; file: string; created: string | null; modified: string | null; group: number | null }
export type BidVerdict = 'yes' | 'no' | 'missing' | 'unclear' | 'manual'
export type BidReviewStatus = 'pending' | 'verified' | 'excluded'
export const bidReviewLabel: Record<BidReviewStatus, string> = { pending: '待核对', verified: '已核实', excluded: '已排除' }
export interface BidReview { review_status: BidReviewStatus; note: string | null }
export interface BidCell extends BidReview { requirement_id: string; bidder_id: string; verdict: BidVerdict; locs: { file_id: string; loc: string; page: number | null }[]; reason: string | null; source: 'local' | 'model'; cite: null | 'verified' | 'unverified'; conclusion_tier?: BidTier | null }
/** 报价分析（V34-B-04 计算，B-06 展示）：金额已格式化为字符串 */
export interface BidPricing {
  bidders: Record<string, { total: string | null; totals_found: string[]; currency: string | null; manual_total?: boolean; table: { name: string; rows: number; total: string | null } | null; over_ceiling: string | null
    /** 多个不同总价时最高者与限价之差（V34Q-25）；over_ceiling 只对应唯一总价 */
    over_ceiling_max?: string | null
    /** 总价比较（V34Q-27，后端 Decimal）：最高 / 最低、比最低价高、偏离平均、异常低价；not_compared 为未参加比较的原因 */
    mark?: 'highest' | 'lowest' | null; above_lowest?: string | null; above_lowest_pct?: string | null; vs_mean?: string | null
    low_price?: { others: number; others_mean: string; below_pct: string } | null; not_compared?: string | null
    /** 唯一总价的出处（报价标题、文件、页码） */
    source?: { label: string; file_id: string | null; page: number | null } | null
    /** “找报价页”的候选页（按命中次数排序，最多 10 页） */
    quote_pages?: { file_id: string; page: number; hits: number }[]
    /** 唱标价（人工录入，已格式化）与文件中识别的总价 */
    opening?: { amount: string; upper: string | null; note: string | null } | null; file_total?: string | null
    /** 修正后总价（大写为准、单价为准），供评标委员会参考 */
    corrected?: { total: string; from: string; basis: string; over_ceiling?: string } | null }>
  compare?: { n: number; highest: string; lowest: string; mean: string; spread_pct: string | null } | null
  /** 三家及以上总价等差 / 等比（ids 按金额从低到高） */
  total_patterns?: { ids: string[]; kind: 'diff' | 'ratio'; step: string; level: BidLevel }[]
  items: { name: string; prices: Record<string, string | null>; fixed: boolean }[]
  pairs: { a: string; b: string; aligned: number; aligned_by: 'name' | 'row'; n?: number; mean_ratio?: string; cv?: string; level?: BidLevel; label?: string }[]
  aligned_by: Record<string, 'name' | 'row'>; base?: string
}
export type BidSignMark = 'stamped' | 'missing' | 'unclear'
export interface BidSigning { bidder_id: string; file_id: string; file: string; page: number | null; loc: string; title: string; renderable: boolean; mark: BidSignMark | null }
export type BidTier = 'certain' | 'verify' | 'reference'
export type BidReviewReason = 'template' | 'manufacturer' | 'recognition' | 'model' | 'other'
export interface BidConclusionEntry {
  id: string; bidder_ids: string[]; title: string; category: string; tier: BidTier
  item_id: string | null; requirement_id: string | null; case_id: string | null; article: string | null
}
export interface BidCase {
  id: string; bidder_ids: string[]; title: string; article: string; tier: BidTier
  evidence: { item_id: string; row: number; fact: string; bidder_ids: string[] }[]
  normal: string[]; baseline: { sentences?: number; images?: number }
}
export interface BidConclusionDecision {
  status: BidReviewStatus; entry: BidConclusionEntry; reason_label: string | null; detail: string | null; linked: string[]
}
export interface BidConclusionReview {
  key: string; status: BidReviewStatus; reason?: BidReviewReason; detail?: string; remember?: boolean
}
export interface BidConclusionData {
  lede: string; sub: string | null; certain: BidConclusionEntry[]; verify: BidConclusionEntry[]; reference: BidConclusionEntry[]
  cases: BidCase[]; reviews: Record<string, BidConclusionDecision>; signed: string | null; second_calls: number
  site: { id: string; text: string; seq: number }[]
  bidders: { id: string; name: string; short: string; tier: BidTier | null; total: string | null; manual_total: boolean; reasons: { id: string; title: string }[] }[]
  /** 报价分析页与报告共用（V34-B-06e）：结论句、每家结论标签、报价方面需核实的条目 id */
  pricing: { lede: string; tags: Record<string, { key: string; text: string; tier: BidTier }[]>; verify: string[] }
}
export interface BidFeedback {
  rules: { id: string; agency: string; label: string; reason: string }[]
  stats: { category: string; verified: number; total: number }[]
}
export interface BidCheck {
  conclusion: BidConclusionData
  state: BidProject['check']['state']; error: string | null; coverage: string[]; items: BidItem[]
  steps: string[]; elapsed: Record<string, number>; cells: BidCell[]; pricing: BidPricing | null
  parameters: { ceiling: string | null; currency: string | null }
  requirements: { id: string; group: BidGroup; text: string; method: BidMethod; seq: number; not_applicable?: boolean }[]
  signing: BidSigning[]
  timeline?: BidTimelineFile[]
}
export interface BidCellPlace { file_id: string; file: string; page: number | null; renderable: boolean; before: string; text: string; after: string }
export type BidCellUpdate = BidReview & Partial<Pick<BidCell, 'verdict' | 'source' | 'cite' | 'reason' | 'locs'>>
export interface BidCellDetail {
  requirement: { id: string; text: string; group: BidGroup; method: BidMethod; seq: number; sources: { file: string; text: string }[] }
  bidder: { id: string; name: string }
  cell: (BidReview & { verdict: BidVerdict; source: 'local' | 'model'; cite: BidCell['cite']; reason: string | null }) | null
  places: BidCellPlace[]
  second?: { outcome: 'found' | 'confirmed' | 'unclear'; verdict?: BidVerdict | null; reason?: string | null } | null
}
export const bidLevelLabel: Record<BidLevel, string> = { strong: '强', medium: '中', weak: '弱', normal: '正常重复', veto: '疑似否决项', general: '一般问题', hint: '提示' }
export interface BidSettings { title: string; opening_date: string; policy: 'ai' | 'local_only'; category_id: number | null; folder_id?: string | null }
export const bidStageLabel: Record<BidStage, string> = { preparing: '准备中', ready: '待开标', checking: '初审中', checked: '待核对' }
export const bidPolicyLabel: Record<BidSettings['policy'], string> = { local_only: '只做本地检查', ai: '本机检查 + 模型核对' }
/** 列表与项目页共用的状态：文字 + 圆点色调（visual.md 第五节“圆点 + 文字”） */
export function bidState(p: { stage: BidStage; status: string }): { label: string; tone: 'run' | 'warn' | 'ok' | 'idle' } {
  if (p.status === 'parsing') return { label: '解析中', tone: 'run' }
  if (p.status === 'parse_failed') return { label: '文件待处理', tone: 'warn' }
  if (p.stage === 'checking') return { label: '初审中', tone: 'run' }
  if (p.stage === 'checked') return { label: '待核对', tone: 'warn' }
  return { label: bidStageLabel[p.stage], tone: p.stage === 'ready' ? 'ok' : 'idle' }
}
export function formatSize(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`
}
export const bidApi = {
  sent: (id: string) => http.get<{ seq: number; purpose: string; created_at: string; status: 'sent' | 'blocked' | 'error'; chars: number | null; text: string | null }[]>(`/bid/${id}/sent`),
  list: (query: BidListQuery = {}) => http.get<{ items: BidSummary[]; total: number; checking_count: number }>('/bid', Object.fromEntries(Object.entries(query).filter(([, v]) => v !== undefined && v !== '' && v !== null))),
  folders: (query: Omit<BidListQuery, 'page' | 'folder'> = {}) => http.get<BidFolderTree>('/bid/folders', Object.fromEntries(Object.entries(query).filter(([, v]) => v !== undefined && v !== '' && v !== null))),
  createFolder: (name: string, parent_id: string | null) => http.post<BidFolderTree>('/bid/folders', { name, parent_id }),
  renameFolder: (fid: string, name: string) => http.patch<BidFolderTree>(`/bid/folders/${fid}`, { name }),
  removeFolder: (fid: string) => http.delete<BidFolderTree>(`/bid/folders/${fid}`),
  place: (id: string, folder_id: string | null) => http.put<{ folder_id: string | null }>(`/bid/${id}/folder`, { folder_id }),
  categories: () => http.get<{ items: BidCategory[] }>('/bid/categories'),
  adminCategories: () => http.get<{ items: BidCategory[] }>('/admin/bid-categories'),
  addCategory: (name: string) => http.post<{ items: BidCategory[] }>('/admin/bid-categories', { name }),
  patchCategory: (cid: number, body: { name?: string; enabled?: boolean }) => http.patch<{ items: BidCategory[] }>(`/admin/bid-categories/${cid}`, body),
  removeCategory: (cid: number) => http.delete<{ items: BidCategory[] }>(`/admin/bid-categories/${cid}`),
  create: (body: BidSettings) => http.post<BidProject>('/bid', body),
  get: (id: string) => http.get<BidProject>(`/bid/${id}`),
  update: (id: string, body: BidSettings) => http.patch<BidProject>(`/bid/${id}`, body),
  remove: (id: string) => http.delete<void>(`/bid/${id}`),
  addBidder: (id: string, name: string) => http.post<BidProject>(`/bid/${id}/bidders`, { name }),
  renameBidder: (id: string, bidder: string, name: string) => http.patch<BidProject>(`/bid/${id}/bidders/${bidder}`, { name }),
  removeBidder: (id: string, bidder: string) => http.delete<BidProject>(`/bid/${id}/bidders/${bidder}`),
  upload: (id: string, files: File[], bidder?: string, groups?: string[], onProgress?: (loaded: number, total: number) => void) => {
    const form = new FormData()
    files.forEach(f => form.append('files', f))
    if (bidder) form.append('bidder_id', bidder)
    if (groups) form.append('groups', JSON.stringify(groups))
    return http.postFormWithProgress<BidProject>(`/bid/${id}/files`, form, onProgress ?? (() => {}))
  },
  removeFile: (id: string, fid: string) => http.delete<BidProject>(`/bid/${id}/files/${fid}`),
  password: (id: string, fid: string, password: string) => http.put<BidProject>(`/bid/${id}/files/${fid}/password`, { password }),
  reveal: (id: string, fid: string) => http.get<{ password: string | null }>(`/bid/${id}/files/${fid}/password`),
  parse: (id: string) => http.post<BidProject>(`/bid/${id}/parse`),
  tender: (id: string, view: 'original' | 'ai' = 'original') => http.get<BidTender>(`/bid/${id}/tender`, { view }),
  requirements: (id: string) => http.get<BidRequirements>(`/bid/${id}/requirements`),
  parameters: (id: string, body: BidParameters) => http.put<BidRequirements>(`/bid/${id}/parameters`, body),
  addRequirement: (id: string, body: { group: BidGroup; text: string; method: BidMethod; locs?: BidLoc[] }) => http.post<BidRequirements>(`/bid/${id}/requirements`, body),
  patchRequirement: (id: string, rid: string, body: Partial<Pick<BidRequirement, 'group' | 'text' | 'method' | 'enabled'>>) => http.patch<BidRequirements>(`/bid/${id}/requirements/${rid}`, body),
  removeRequirement: (id: string, rid: string) => http.delete<BidRequirements>(`/bid/${id}/requirements/${rid}`),
  reextract: (id: string) => http.post<BidRequirements>(`/bid/${id}/requirements/extract`),
  assist: (id: string, action: 'start' | 'confirm' | 'cancel') => http.post<BidRequirements>(`/bid/${id}/assist/${action}`),
  confirm: (id: string) => http.post<BidRequirements>(`/bid/${id}/confirm`),
  startCheck: (id: string) => http.post<BidProject>(`/bid/${id}/check`),
  check: (id: string) => http.get<BidCheck>(`/bid/${id}/check`),
  model: (id: string) => http.get<BidModelView>(`/bid/${id}/model`),
  conclusionReview: (id: string, body: BidConclusionReview) => http.post<BidCheck>(`/bid/${id}/conclusion/review`, body),
  /** 唱标价：初审完成后返回检查结果（已重算报价），之前返回项目 */
  openingPrice: (id: string, bidder: string, body: BidOpeningPriceInput) => http.put<BidCheck>(`/bid/${id}/bidders/${bidder}/opening-price`, body),
  openingPriceBefore: (id: string, bidder: string, body: BidOpeningPriceInput) => http.put<BidProject>(`/bid/${id}/bidders/${bidder}/opening-price`, body),
  applicability: (id: string, rid: string, applicable: boolean) => http.put<BidCheck>(`/bid/${id}/requirements/${rid}/applicability`, { applicable }),
  conclusionSign: (id: string, signed: boolean) => http.put<BidCheck>(`/bid/${id}/conclusion/sign`, { signed }),
  feedback: () => http.get<BidFeedback>('/bid/feedback/mine'),
  revokeFeedback: (token: string) => http.delete<void>(`/bid/feedback/mine/${token}`),
  modelAction: (id: string, action: 'prepare' | 'prepare_second' | 'confirm' | 'cancel') => http.post<BidModelView>(`/bid/${id}/model/${action}`),
  reviewItem: (id: string, iid: string, body: Partial<BidReview>) => http.patch<BidReview>(`/bid/${id}/items/${iid}`, body),
  reviewCell: (id: string, body: Partial<BidReview> & { requirement_id: string; bidder_id: string }) => http.put<BidReview>(`/bid/${id}/cells`, body),
  cell: (id: string, requirement_id: string, bidder_id: string) => http.get<BidCellDetail>(`/bid/${id}/cell`, { requirement_id, bidder_id }),
  sign: (id: string, s: Pick<BidSigning, 'file_id' | 'page' | 'loc'>, mark: BidSignMark | null) => http.put<{ marks: Record<string, string> }>(`/bid/${id}/signing`, { file_id: s.file_id, page: s.page, loc: s.loc, mark }),
  /** 批量核对（V34-B-06c）：一次事务，任一项不属于本项目则全部不改 */
  reviewBatch: (id: string, body: { items?: string[]; cells?: { requirement_id: string; bidder_id: string }[]; review_status?: BidReviewStatus; signing?: Pick<BidSigning, 'file_id' | 'page' | 'loc'>[]; mark?: BidSignMark | null }) =>
    http.post<{ items: number; cells: number; cell_updates: (BidCellUpdate & Pick<BidCell, 'requirement_id' | 'bidder_id'>)[]; marks: Record<string, string> | null }>(`/bid/${id}/review`, body),
  reopen: (id: string) => http.post<BidProject>(`/bid/${id}/reopen`),
  /** 投标文件页面图（cookie 会话，直接作 img 地址；服务器内存生成、不缓存） */
  pageUrl: (id: string, fid: string, page: number, width = 320) => `/api/v1/bid/${id}/files/${fid}/page/${page}?width=${width}`,
}
