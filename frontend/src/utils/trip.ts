/**
 * 往返线「衡线」的状态映射（澄明二版规范第二节）。状态到站点的对应只写在这里，各页不得自己判断。
 *
 * 一件工作从本机出发，遮住后经网关到模型，再回到本机还原。每个模块有自己的站名（工具条尺度），
 * 迷你尺度一律压成通用五站（或只在本机的三站），几站压进同一通用站时当前站的状态说了算。
 */

export type TripModule = 'review' | 'chat' | 'bid' | 'bidx' | 'meet' | 'transL' | 'transC' | 'local'
/** 未到、进行中、等你、被拦截、失败、已停止、全线完成、部分完成（还原已过，模型站带琥珀光圈） */
export type TripState = 'todo' | 'run' | 'wait' | 'block' | 'fail' | 'stop' | 'done' | 'partial'

export interface Trip {
  mod: TripModule
  /** 当前站（模块自己的站名序号） */
  at: number
  state: TripState
  /** 当前站旁边的一句话：真实数量进度、原因、等你做什么 */
  note?: string
  /** 等你、被拦截、失败时紧跟的动作按钮文字（只在工具条尺度显示） */
  act?: string
}

interface TripDef { names: string[]; mini?: number[]; local?: boolean }
export const TRIP_DEFS: Record<TripModule, TripDef> = {
  review: { names: ['读取', '确认遮住', '复检', '模型审核', '还原'] },
  chat: { names: ['读取附件', '遮住', '复检', '模型回答', '还原'] },
  bid: { names: ['读取材料', '识别扫描页', '本机检查', '完成'], mini: [0, 1, 1, 2], local: true },
  bidx: { names: ['读取材料', '识别扫描页', '本机检查', '遮住', '复检', '模型补查', '还原'], mini: [0, 0, 0, 1, 2, 3, 4] },
  meet: { names: ['本机转写', '校对', '确认遮住', '复检', '生成纪要', '还原'], mini: [0, 0, 1, 2, 3, 4] },
  transL: { names: ['读取', '本机翻译', '完成'], local: true },
  transC: { names: ['读取', '确认遮住', '复检', '模型翻译', '还原'] },
  local: { names: ['读取', '本机处理', '完成'], local: true },
}
const GEN5 = ['读取', '遮住', '复检', '模型', '还原']
const GEN3 = ['读取', '本机处理', '完成']
export const STATE_WORD: Record<TripState, string> = { todo: '未开始', run: '进行中', wait: '等你', block: '被拦截', fail: '失败', stop: '已停止', done: '已完成', partial: '部分完成' }

/** 站名是否在云端（模型站），决定站色与线段色 */
const isModel = (name: string) => /模型|生成纪要/.test(name)

export type StationState = 'todo' | 'done' | 'run' | 'wait' | 'block' | 'fail' | 'stop'
export interface Station { name: string; zone: 'l' | 'm'; state: StationState; partial: boolean }
/** 线段：l 本机青、m 有一端是模型为紫、r 从模型回到还原（前半紫后半青）；pass 为已走过 */
export type Segment = { zone: 'l' | 'm' | 'r'; pass: boolean }

/** 每站状态：at 之前已过，at 为当前，之后未到；done / partial 全线已过 */
export function stationsOf(names: string[], at: number, state: TripState): Station[] {
  return names.map((name, i) => {
    let s: StationState = 'todo'
    if (state === 'done' || state === 'partial') s = 'done'
    else if (i < at) s = 'done'
    else if (i === at) s = state === 'todo' ? 'todo' : state
    return { name, zone: isModel(name) ? 'm' : 'l', state: s, partial: state === 'partial' && isModel(name) }
  })
}
export function segmentsOf(ss: Station[]): Segment[] {
  return ss.slice(1).map((b, i) => {
    const a = ss[i]
    const zone = a.zone === 'm' && b.zone === 'l' ? 'r' : a.zone === 'm' || b.zone === 'm' ? 'm' : 'l'
    return { zone, pass: a.state === 'done' && b.state !== 'todo' }
  })
}
/** 工具条尺度：模块自己的站 */
export function barStations(t: Trip): Station[] { return stationsOf(TRIP_DEFS[t.mod].names, t.at, t.state) }
/** 迷你尺度：压成通用五站或本机三站 */
export function miniStations(t: Trip): Station[] {
  const def = TRIP_DEFS[t.mod], gen = def.local ? GEN3 : GEN5
  const map = def.mini ?? def.names.map((_, i) => i)
  const at = t.state === 'done' || t.state === 'partial' ? gen.length - 1 : map[Math.min(t.at, def.names.length - 1)]
  return stationsOf(gen, at, t.state)
}
export const isLocalTrip = (t: Trip) => !!TRIP_DEFS[t.mod].local
export const currentName = (t: Trip) => TRIP_DEFS[t.mod].names[t.at] ?? ''
/** 读屏文字，例如“第 2 站 确认遮住：等你，2 份待确认” */
export function tripAria(t: Trip): string {
  const def = TRIP_DEFS[t.mod]
  if (t.state === 'done') return `${def.names.length} 站全部完成${def.local ? '，全程本机' : ''}${t.note ? '：' + t.note : ''}`
  if (t.state === 'partial') return `${def.names.length} 站已走完，模型部分完成${t.note ? '：' + t.note : ''}`
  return `第 ${t.at + 1} 站 ${def.names[t.at]}：${STATE_WORD[t.state]}${t.note ? '，' + t.note : ''}`
}
/** 悬停提示：逐站列出 */
export function tripTitle(t: Trip): string {
  const finished = t.state === 'done' || t.state === 'partial'
  return TRIP_DEFS[t.mod].names.map((n, i) => (finished || i < t.at ? '✓ ' : i === t.at ? '● ' : '○ ') + n).join('　')
}

/* ================= 各模块的映射 ================= */

/** 文件审核（旧审核任务与发起过审核的会话）：任务状态 + 最新一轮状态 */
export function reviewTrip(p: { status: string; policy?: string | null; runStatus?: string | null; reviewing?: boolean; blocked?: number; queue?: number | null }): Trip {
  if (p.policy === 'local_only') return localTrip(p.status)
  const s = p.status, r = p.runStatus
  if (s === 'parsing' || s === 'draft') return { mod: 'review', at: 0, state: 'run', note: '正在读取' }
  if (s === 'parse_failed') return { mod: 'review', at: 0, state: 'fail', note: '读取失败', act: '重试' }
  if (r === 'blocked' || p.blocked) return { mod: 'review', at: 2, state: 'block', note: p.blocked ? `发送前复检拦截 ${p.blocked} 处` : '发送前复检拦截', act: '去处理' }
  if (s === 'awaiting_confirm') return { mod: 'review', at: 1, state: 'wait', note: '等你确认', act: '去确认' }
  if (r === 'queued' || r === 'running' || p.reviewing) return { mod: 'review', at: 3, state: 'run', note: p.queue ? `排队中 · 前面 ${Math.max(0, p.queue - 1)} 个` : '模型审核中' }
  if (r === 'failed') return { mod: 'review', at: 3, state: 'fail', note: '审核未完成', act: '重试' }
  if (r === 'partial') return { mod: 'review', at: 4, state: 'partial', note: '部分完成' }
  if (r === 'completed' || s === 'reviewed') return { mod: 'review', at: 4, state: 'done' }
  if (s === 'completed_local') return localTrip(s)
  // 已确认遮住、还没发起：停在复检前
  return { mod: 'review', at: 2, state: 'todo', note: '待发起审核' }
}

/** 外发策略为只在本机处理：读取 · 本机处理 · 完成，整条青色 */
export function localTrip(status: string): Trip {
  if (status === 'parsing' || status === 'draft') return { mod: 'local', at: 0, state: 'run', note: '正在读取' }
  if (status === 'parse_failed') return { mod: 'local', at: 0, state: 'fail', note: '读取失败' }
  return { mod: 'local', at: 2, state: 'done' }
}

/** 对话：读取附件 · 遮住 · 复检 · 模型回答 · 还原 */
export function chatTrip(p: { status: string; policy?: string | null; generating?: boolean }): Trip {
  if (p.policy === 'local_only') return localTrip(p.status)
  if (p.status === 'parsing') return { mod: 'chat', at: 0, state: 'run', note: '正在读取' }
  if (p.status === 'parse_failed') return { mod: 'chat', at: 0, state: 'fail', note: '读取失败' }
  if (p.status === 'awaiting_confirm') return { mod: 'chat', at: 1, state: 'wait', note: '等你确认', act: '去确认' }
  if (p.generating) return { mod: 'chat', at: 3, state: 'run', note: '正在回答' }
  return { mod: 'chat', at: 4, state: 'done' }
}

/** 标书初审：本机三站；发起模型补查后接上遮住 · 复检 · 模型补查 · 还原 */
export function bidTrip(p: { status: string; stage?: string | null; modelState?: string | null; progress?: { done: number; total: number } | null }): Trip {
  if (p.status === 'parsing') return { mod: 'bid', at: 0, state: 'run', note: '正在读取' }
  if (p.status === 'parse_failed') return { mod: 'bid', at: 0, state: 'fail', note: '文件待处理' }
  const m = p.modelState
  if (m) {
    if (m === 'preparing') return { mod: 'bidx', at: 3, state: 'run', note: '正在遮住' }
    if (m === 'awaiting') return { mod: 'bidx', at: 3, state: 'wait', note: '等你确认', act: '去确认' }
    if (m === 'running') return { mod: 'bidx', at: 5, state: 'run', note: '模型补查中' }
    if (m === 'failed') return { mod: 'bidx', at: 5, state: 'fail', note: '补查未完成', act: '重试' }
    return { mod: 'bidx', at: 6, state: 'done' }
  }
  if (p.stage === 'checking') {
    const pr = p.progress
    return { mod: 'bid', at: 2, state: 'run', note: pr?.total ? `${Math.round(pr.done / pr.total * 100)}%` : '本机检查中' }
  }
  if (p.stage === 'checked') return { mod: 'bid', at: 3, state: 'done' }
  return { mod: 'bid', at: 2, state: 'todo', note: p.stage === 'ready' ? '待开标' : '材料准备中' }
}

type MeetingState = 'transcribing' | 'failed' | 'proofread' | 'confirmed' | 'minuted'
/** 会议纪要：本机转写 · 校对 · 确认遮住 · 复检 · 生成纪要 · 还原 */
export function meetingTrip(p: { state?: MeetingState | null; status?: string; generating?: boolean; policy?: string | null; percent?: number | null }): Trip {
  const s = p.state
  if (!s) return p.status === 'parsing' ? { mod: 'meet', at: 0, state: 'run', note: '正在转写' } : { mod: 'meet', at: 0, state: 'todo', note: '待上传录音' }
  if (s === 'transcribing') return { mod: 'meet', at: 0, state: 'run', note: p.percent != null ? `${p.percent}%` : '正在转写' }
  if (s === 'failed') return { mod: 'meet', at: 0, state: 'fail', note: '转写失败', act: '重试' }
  if (s === 'proofread') return { mod: 'meet', at: 1, state: 'wait', note: '待校对', act: '去校对' }
  if (p.generating) return { mod: 'meet', at: 4, state: 'run', note: '正在生成纪要' }
  if (s === 'minuted') return { mod: 'meet', at: 5, state: 'done' }
  if (p.policy === 'local_only') return { mod: 'meet', at: 1, state: 'done' }
  return { mod: 'meet', at: 4, state: 'wait', note: '待生成纪要', act: '生成纪要' }
}

/** 翻译：本机三站（读取 · 本机翻译 · 完成），或云端五站（读取 · 确认遮住 · 复检 · 模型翻译 · 还原） */
export function translateTrip(p: { status: string; engine?: 'local' | 'cloud' | null; translationStatus?: string | null; confirmed?: boolean; percent?: number | null }): Trip {
  const cloud = p.engine === 'cloud', mod: TripModule = cloud ? 'transC' : 'transL', work = cloud ? 3 : 1
  if (p.status === 'parsing') return { mod, at: 0, state: 'run', note: '正在读取' }
  if (p.status === 'parse_failed') return { mod, at: 0, state: 'fail', note: '读取失败' }
  const t = p.translationStatus
  if (t === 'queued' || t === 'streaming') return { mod, at: work, state: 'run', note: p.percent != null ? `${p.percent}%` : '正在翻译' }
  if (t === 'done') return { mod, at: cloud ? 4 : 2, state: 'done' }
  if (t === 'failed') return { mod, at: work, state: 'fail', note: '翻译未完成', act: '重试' }
  if (t === 'stopped') return { mod, at: work, state: 'stop', note: '已停止' }
  if (cloud && (p.status === 'awaiting_confirm' || p.confirmed === false)) return { mod, at: 1, state: 'wait', note: '等你确认', act: '去确认' }
  return { mod, at: work, state: 'todo', note: '待翻译' }
}

/** 文件审核工作区顶部（Assistant 的审核视图）：ReviewPane 上报的进度 → 往返线 */
export interface ReviewProgress {
  stage: 'parsing' | 'confirm' | 'setup' | 'running' | 'result' | 'local' | 'loading'
  /** 审核中：开始时间（ISO），用于计时 */
  since?: string | null
  /** 已完成：用时（秒） */
  seconds?: number | null
  /** 发送前复检拦下的处数（确认页） */
  blocked?: number
  /** 审核中：排队位置 */
  queue?: number | null
  /** 结果的轮次状态（failed / partial 等） */
  status?: string
}
export function progressTrip(p: ReviewProgress): Trip | null {
  switch (p.stage) {
    case 'loading': return null
    case 'local': return localTrip('completed_local')
    case 'parsing': return reviewTrip({ status: 'parsing' })
    // 工作区里确认页就在眼前，不再带“去确认 / 去处理”按钮
    case 'confirm': return p.blocked ? { ...reviewTrip({ status: 'awaiting_confirm', blocked: p.blocked }), act: undefined } : { mod: 'review', at: 1, state: 'wait', note: '等你确认' }
    case 'setup': return reviewTrip({ status: 'active' })
    case 'running': { const t = reviewTrip({ status: 'reviewing', runStatus: 'running', queue: p.queue }); return { ...t, note: `${t.note} · 可以离开页面` } }
    case 'result': return reviewTrip({ status: 'reviewed', runStatus: p.status ?? 'completed' })
  }
}

/* ================= 列表行 ================= */

interface RowLike {
  kind?: 'session' | 'review' | 'bid'; purpose?: 'chat' | 'meeting' | 'translate' | null; status?: string
  policy?: string | null; run_status?: string | null; reviewing?: boolean; blocked?: boolean; is_review?: boolean; run_count?: number
  generating?: boolean; meeting?: { state: MeetingState } | null; translation_engine?: 'local' | 'cloud' | null; translation_status?: string | null
  stage?: string | null; model_state?: string | null
}
/** 历史记录、近期会话、全局搜索、工作台待办的一行 → 往返线 */
export function rowTrip(row: RowLike): Trip {
  const status = row.status ?? 'active'
  if (row.kind === 'bid' || (row.stage !== undefined && row.kind === undefined && row.purpose === undefined)) return bidTrip({ status, stage: row.stage, modelState: row.model_state })
  if (row.purpose === 'meeting') return meetingTrip({ state: row.meeting?.state, status, generating: row.generating, policy: row.policy })
  if (row.purpose === 'translate') return translateTrip({ status, engine: row.translation_engine, translationStatus: row.translation_status })
  if (row.kind === 'review' || row.is_review || row.run_count) return reviewTrip({ status, policy: row.policy, runStatus: row.run_status ?? (row.blocked ? 'blocked' : null), reviewing: row.reviewing })
  return chatTrip({ status, policy: row.policy, generating: row.generating })
}
