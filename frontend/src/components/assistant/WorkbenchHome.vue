<script setup lang="ts">
// 工作台首页（澄明 8.2）：氛围光 → 问候 → 输入框（插槽）→ 快捷开始 → 四张模块卡 → 待你处理 → 信任条。
// busy：已加入文件、还没发出第一条消息的会话，只留问候、输入框、快捷开始与“只在本机”说明。
// 数据只取本人已有的接口（历史记录第一页、等你处理计数、会议与翻译列表、标书列表），失败时卡片照常可点、只是不写状态。
import { computed, onBeforeUnmount, onMounted, ref, type Component } from 'vue'
import { useRouter } from 'vue-router'
import { AudioLines, Columns2, EyeOff, FileSearch, FileX, ListChecks, Lock, PenLine, ShieldAlert, ShieldCheck, Sparkle } from '@lucide/vue'
import { assistantApi, type HistoryItem, type MyStats, type Template, type Waiting } from '@/api/assistant'
import { bidApi } from '@/api/bid'
import { useAuthStore } from '@/stores/auth'
import SiLogo from '@/components/brand/SiLogo.vue'
import SiCount from '@/components/ui/SiCount.vue'

const props = defineProps<{ busy?: boolean; hasFiles?: boolean; template: Template | null }>()
const emit = defineEmits<{ 'update:template': [value: Template | null]; review: [] }>()
const auth = useAuthStore(), router = useRouter()
const can = (p: Parameters<typeof auth.can>[0]) => auth.can(p)

// 问候：只取北京时间和当前账号显示名，不发送到服务器（V33Q-14）
const hourFormat = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Shanghai', hour: '2-digit', hourCycle: 'h23' })
const hour = ref(Number(hourFormat.format(new Date())))
const timer = setInterval(() => { if (!document.hidden) hour.value = Number(hourFormat.format(new Date())) }, 60000)
onBeforeUnmount(() => clearInterval(timer))
const greeting = computed(() => {
  const h = hour.value
  const word = h < 5 || h >= 23 ? '夜深了' : h < 8 ? '早上好' : h < 11 ? '上午好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好'
  const name = auth.user?.display_name?.trim() || auth.user?.username
  return name ? `${word}，${name}` : word
})

// 快捷开始：全面审核去文件审核（会话里有文件时进本会话的审核视图），其余三项只选中输入框的模板
interface Quick { key: string; icon: Component; label: string; template?: Template }
const quick = computed<Quick[]>(() => [
  ...(can('use.review') ? [{ key: 'review', icon: FileSearch, label: '全面审核这份合同' }] : []),
  ...(can('use.assistant') ? [
    ...(can('use.review') ? [] : [{ key: 'summary', icon: FileSearch, label: '提取文件重点', template: 'summary' as Template }]),
    { key: 'reply', icon: PenLine, label: '起草回复函', template: 'reply' as Template },
    { key: 'crosscheck', icon: ListChecks, label: '核对单证', template: 'crosscheck' as Template },
    { key: 'compare', icon: Columns2, label: '比较几份报价', template: 'compare' as Template },
  ] : []),
])
function pickQuick(item: Quick) {
  if (!item.template) { emit('review'); return }
  emit('update:template', props.template === item.template ? null : item.template)
}

// ---- 模块卡与待你处理的数据 ----
const history = ref<HistoryItem[]>([]), waiting = ref<Waiting | null>(null), stats = ref<MyStats | null>(null)
const lastMeeting = ref<{ title: string; at: string } | null>(null), lastTranslate = ref<{ title: string; at: string } | null>(null)
const lastBid = ref<{ title: string; at: string } | null>(null), bidChecking = ref(0)
const asrEnabled = ref(true), mtEnabled = ref(false), loaded = ref(false)
const TZ = 'Asia/Shanghai'
onMounted(async () => {
  if (props.busy) return
  const skip = Promise.reject(new Error('skip'))
  skip.catch(() => undefined)
  const [h, w, m, t, b, st] = await Promise.allSettled([
    assistantApi.history({ page: 1 }), assistantApi.waiting(),
    can('use.meeting') ? assistantApi.list(1, '', 'meeting') : skip,
    assistantApi.list(1, '', 'translate'),
    can('use.bid') ? bidApi.list() : skip,
    assistantApi.stats(),
  ])
  if (h.status === 'fulfilled') history.value = h.value.items
  if (w.status === 'fulfilled') waiting.value = w.value
  const real = (i: { title: string; file_count: number }) => i.file_count > 0 || i.title !== '新会话'
  if (m.status === 'fulfilled') { asrEnabled.value = m.value.asr_enabled; const x = m.value.items.find(real); if (x) lastMeeting.value = { title: x.title, at: x.last_activity_at } }
  if (t.status === 'fulfilled') { mtEnabled.value = t.value.mt_enabled; const x = t.value.items.find(real); if (x) lastTranslate.value = { title: x.title, at: x.last_activity_at } }
  if (b.status === 'fulfilled') { bidChecking.value = b.value.checking_count; const x = b.value.items[0]; if (x) lastBid.value = { title: x.title, at: x.last_activity_at } }
  if (st.status === 'fulfilled') stats.value = st.value
  loaded.value = true
})
const monthDay = new Intl.DateTimeFormat('zh-CN', { timeZone: TZ, month: '2-digit', day: '2-digit' })
const md = (at: string) => monthDay.format(new Date(at)).replace('/', '-')
const last = (x: { title: string; at: string } | null) => x ? `上次：${x.title} ${md(x.at)}` : ''

type ModKey = 'review' | 'bid' | 'meet' | 'trans'
interface Mod { key: ModKey; to: string; title: string; desc: string; status: string; warn: boolean }
const reviewed = (r: HistoryItem) => r.kind === 'review' || (r.purpose === 'chat' && r.run_count > 0)
const mods = computed<Mod[]>(() => {
  const w = waiting.value, list: Mod[] = []
  if (can('use.review')) {
    const running = history.value.filter(r => reviewed(r) && r.reviewing).length, lastReview = history.value.find(reviewed)
    list.push({ key: 'review', to: '/review', title: '文件审核', desc: '合同、报价、订单、函件，逐条给出意见', warn: !!w?.review,
      status: w?.review ? `${w.review} 份等你确认` : running ? `${running} 份审核中` : lastReview ? last({ title: lastReview.title, at: lastReview.last_activity_at }) : '拖入文件即可开始' })
  }
  if (can('use.bid')) list.push({ key: 'bid', to: '/bid', title: '标书初审', desc: '开标时找出否决类问题与串标线索', warn: !!w?.bid,
    status: w?.bid ? `${w.bid} 个项目等你确认` : bidChecking.value ? `${bidChecking.value} 个项目初审中` : last(lastBid.value) || '新建项目即可开始' })
  if (can('use.meeting')) list.push({ key: 'meet', to: '/meeting', title: '会议纪要', desc: '本机转写录音，整理议题、决议与待办', warn: !!w?.meeting,
    status: w?.meeting ? `${w.meeting} 场待校对` : last(lastMeeting.value) || (asrEnabled.value ? '录音只在本机转写' : '转写服务未启用') })
  // 翻译与侧栏一致，对所有人显示（没有云端翻译权限时页面内说明）
  list.push({ key: 'trans', to: '/translate', title: '翻译', desc: '本地翻译不外发，或遮住名称后云端翻译', warn: !!w?.translate,
    status: w?.translate ? `${w.translate} 份等你确认` : last(lastTranslate.value) || (mtEnabled.value ? '本地翻译 · 不外发' : '遮住名称后云端翻译') })
  return list
})

// 待你处理：只列要你动手的事（待确认、被拦截、转写待校对、读取失败），最多 5 条，没有就整块不显示
interface Todo { id: string; mod: ModKey; icon: Component; title: string; text: string; badge: string; tone: 'warning' | 'success' | 'danger'; action: string; primary: boolean; open: () => void }
function open(r: HistoryItem) {
  if (r.kind === 'bid') { void router.push(`/bid/${r.id}`); return }
  if (r.purpose === 'meeting' || r.purpose === 'translate') { void router.push(`/${r.purpose}/${r.id}`); return }
  if (r.kind === 'session') { void router.push({ path: `/assistant/${r.id}`, query: r.run_count && can('use.review') ? { mode: 'review' } : {} }); return }
  void router.push(r.status === 'draft' ? { name: 'review-new', query: { task: r.id } } : { name: 'review-result', params: { taskId: r.id } })
}
const todos = computed<Todo[]>(() => history.value.flatMap<Todo>(r => {
  const mod: ModKey = r.kind === 'bid' ? 'bid' : r.purpose === 'meeting' ? 'meet' : r.purpose === 'translate' ? 'trans' : 'review'
  const base = { id: r.id, mod, title: r.title, open: () => open(r) }
  if (r.status === 'parse_failed') return [{ ...base, icon: FileX, text: r.kind === 'bid' ? '有投标文件读取失败，需要重新上传或移除' : '文件读取失败，需要重新上传', badge: '失败', tone: 'danger', action: '去处理', primary: false }]
  if (r.kind === 'bid') return []
  if (r.blocked) return [{ ...base, icon: ShieldAlert, text: '发送前复检拦下了未遮住的名称或金额', badge: '被拦截', tone: 'warning', action: '去处理', primary: false }]
  if (r.status !== 'awaiting_confirm') return []
  if (r.purpose === 'meeting') return [{ ...base, icon: AudioLines, text: '转写完成，等你校对后整理纪要', badge: '转写完成', tone: 'success', action: '去校对', primary: false }]
  return [{ ...base, icon: EyeOff, text: '已在本机遮住名称与金额，等你确认后发送', badge: '待你确认', tone: 'warning', action: '去确认', primary: true }]
}))
</script>

<template>
  <div class="hub">
    <div class="aura si-glow" aria-hidden="true"><i /><i /><i /></div>
    <div class="hub-in" :class="{ 'si-stagger': !busy }">
      <div class="greet">
        <SiLogo size="greet" mark-only intro class="greet-mark" />
        <h1 class="greet-title">{{ greeting }}</h1>
        <p class="greet-sub">把合同、标书或录音交给我。原件和真实名称只留在本机。</p>
      </div>
      <div class="composer-slot"><slot /></div>
      <div v-if="quick.length" class="quick" role="group" aria-label="快捷开始">
        <button v-for="item in quick" :key="item.key" type="button" :class="{ active: item.template && template === item.template }" :aria-pressed="item.template ? template === item.template : undefined" @click="pickQuick(item)">
          <component :is="item.icon" :size="16" :stroke-width="1.75" aria-hidden="true" />{{ item.label }}
        </button>
      </div>
      <p v-if="busy && hasFiles" class="busy-note"><span class="local-tag"><Lock :size="13" :stroke-width="2" />只在本机</span>读取和遮住都在公司服务器上完成。可以离开页面，处理完会在“待你处理”里提醒。</p>
      <template v-else>
        <nav v-if="mods.length" class="mods" aria-label="开始一项工作" :style="{ '--cols': mods.length }">
          <router-link v-for="mod in mods" :key="mod.key" :to="mod.to" class="mod" :class="`m-${mod.key}`">
            <div class="illus" aria-hidden="true">
              <svg v-if="mod.key === 'review'" viewBox="0 0 220 104" fill="none">
                <rect x="66" y="12" width="88" height="84" rx="8" class="il-paper" />
                <path d="M80 30h44M80 42h60M80 54h52M80 66h60M80 78h36" class="il-line" />
                <rect x="76" y="48" width="68" height="12" rx="3" class="il-hl" />
                <circle cx="160" cy="22" r="10" class="il-soft" /><path d="m155 22 3.5 3.5L166 18" class="il-stroke" />
              </svg>
              <svg v-else-if="mod.key === 'bid'" viewBox="0 0 220 104" fill="none">
                <rect x="40" y="18" width="62" height="32" rx="6" class="il-paper" /><path d="M50 30h22M50 40h36" class="il-line" />
                <rect x="118" y="18" width="62" height="32" rx="6" class="il-paper" /><path d="M128 30h22M128 40h36" class="il-line" />
                <rect x="40" y="58" width="62" height="32" rx="6" class="il-paper" /><path d="M50 70h22M50 80h36" class="il-line" />
                <rect x="118" y="58" width="62" height="32" rx="6" class="il-paper red" /><path d="M128 70h22M128 80h36" class="il-line red" />
                <circle cx="178" cy="60" r="7" class="il-alert" /><path d="M178 56.5v4M178 63h.01" class="il-alert-s" />
              </svg>
              <svg v-else-if="mod.key === 'meet'" viewBox="0 0 220 104" fill="none">
                <rect v-for="(h, i) in [14, 26, 40, 22, 52, 34, 62, 30, 46, 20, 36, 16]" :key="i" :x="44 + i * 12" :y="52 - h / 2" width="6" :height="h" rx="3" class="il-bar" :style="{ '--d': `${(i % 5) * 0.12}s` }" />
              </svg>
              <svg v-else viewBox="0 0 220 104" fill="none">
                <rect x="34" y="14" width="66" height="78" rx="8" class="il-paper" /><rect x="120" y="14" width="66" height="78" rx="8" class="il-paper" />
                <path d="M46 32h40M46 48h30M46 64h42M46 78h24" class="il-line" />
                <path d="M132 32h36M132 48h42M132 64h28M132 78h38" class="il-line shift" />
                <path d="M100 48h20M100 64h20" class="il-link" />
              </svg>
            </div>
            <div class="mod-b">
              <b>{{ mod.title }}</b>
              <p>{{ mod.desc }}</p>
              <span class="mod-st" :class="{ warn: mod.warn }"><i v-if="mod.warn" />{{ loaded ? mod.status : '' }}</span>
            </div>
          </router-link>
        </nav>
        <section v-if="todos.length" class="todo" aria-label="待你处理">
          <h2 class="todo-h">待你处理<el-tag type="warning" size="small" class="num">{{ todos.length }}</el-tag></h2>
          <div class="todo-list">
            <div v-for="t in todos.slice(0, 5)" :key="t.id" class="td">
              <span class="si-mod-icon td-ic" :class="`m-${t.mod}`"><component :is="t.icon" :size="18" :stroke-width="1.75" /></span>
              <div class="td-main"><b>{{ t.title }}</b><span>{{ t.text }}</span></div>
              <el-tag :type="t.tone" size="small">{{ t.badge }}</el-tag>
              <el-button size="small" :type="t.primary ? 'primary' : undefined" @click="t.open()">{{ t.action }}</el-button>
            </div>
          </div>
        </section>
        <!-- 信任条：本人本月的数字（GET /assistant/sessions/stats）；取不到时写两句事实 -->
        <p v-if="stats" class="trust">
          <span><EyeOff :size="15" :stroke-width="1.75" />本月在本机遮住 <b class="num"><SiCount :value="stats.masked" /></b> 处名称与金额</span><i />
          <span><ShieldCheck :size="15" :stroke-width="1.75" />发送前复检拦截 <b class="num"><SiCount :value="stats.blocked" /></b> 次</span><i />
          <span v-if="stats.daily_cap"><Sparkle :size="15" :stroke-width="1.75" class="ai" />今天模型调用 <b class="num"><SiCount :value="stats.today_calls" /></b> / {{ stats.daily_cap }} 次</span>
          <span v-else><Sparkle :size="15" :stroke-width="1.75" class="ai" />本月模型调用 <b class="num"><SiCount :value="stats.calls" /></b> 次</span>
        </p>
        <p v-else-if="loaded" class="trust">
          <span><EyeOff :size="15" :stroke-width="1.75" />原件和真实名称只留在本机</span><i />
          <span><ShieldCheck :size="15" :stroke-width="1.75" />发给模型前遮住名称与金额，并做发送前复检</span>
        </p>
      </template>
    </div>
  </div>
</template>

<style scoped>
.hub { position: relative; min-height: 100%; }
/* 氛围光：顶部约 420px，底部渐隐到画布 */
.aura { bottom: auto; height: 440px; }
/* 光斑尺寸与位置照设计稿（preview-pages.css .aura）：首屏上方可见的三团光 */
.aura > i:nth-child(1) { left: 8%; top: -260px; width: 720px; height: 520px; aspect-ratio: auto; }
.aura > i:nth-child(2) { left: auto; right: 6%; top: -220px; width: 640px; height: 480px; aspect-ratio: auto; }
.aura > i:nth-child(3) { left: 40%; top: -120px; width: 520px; height: 360px; aspect-ratio: auto; }
.aura::after { content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 160px; background: linear-gradient(transparent, var(--si-bg-canvas)); }
.hub-in { position: relative; z-index: 1; max-width: 1040px; margin: 0 auto; padding: 10vh 40px 48px; display: flex; flex-direction: column; align-items: center; }
.greet { display: flex; flex-direction: column; align-items: center; text-align: center; }
.greet-mark { margin-bottom: 18px; }
.greet-title { margin: 0; font: var(--si-font-greeting); letter-spacing: -.02em; color: var(--si-ink-1); overflow-wrap: anywhere; }
.greet-sub { margin: 10px 0 0; font: 400 calc(16px * var(--si-fs))/calc(26px * var(--si-fs)) var(--si-font-sans); color: var(--si-ink-3); }
.composer-slot { width: 760px; max-width: 100%; margin-top: 36px; }
.quick { display: flex; flex-wrap: wrap; justify-content: center; gap: 10px; margin-top: 16px; }
.quick button { display: inline-flex; align-items: center; gap: 8px; height: 36px; padding: 0 14px; border: 1px solid var(--si-hair); border-radius: 999px; background: var(--si-surface-1); color: var(--si-ink-2); font: var(--si-font-label-sm); cursor: pointer; transition: transform var(--si-d3) var(--si-ease-out), box-shadow var(--si-d3) var(--si-ease-out), border-color var(--si-d2), color var(--si-d2), background-color var(--si-d2); }
.quick button svg { color: var(--si-brand-2); }
.quick button:hover { transform: translateY(-2px); box-shadow: var(--si-shadow-2); border-color: var(--si-hair-strong); color: var(--si-ink-1); }
.quick button.active { border-color: var(--si-brand-line); background: var(--si-blue-tint); color: var(--si-blue-ink); }
.busy-note { display: flex; align-items: center; justify-content: center; gap: 8px; width: 760px; max-width: 100%; margin: 16px 0 0; font: var(--si-font-caption); color: var(--si-ink-3); }
.local-tag { display: inline-flex; flex: none; align-items: center; gap: 6px; height: 24px; padding: 0 10px; border-radius: 999px; background: var(--si-blue-tint); color: var(--si-blue-ink); font: var(--si-font-badge); }

/* 模块卡：插画在上（模块浅底 + 线条），悬停时插画动起来 */
.m-review { --mc: var(--si-m-review); --mc-soft: var(--si-m-review-soft); }
.m-bid { --mc: var(--si-m-bid); --mc-soft: var(--si-m-bid-soft); }
.m-meet { --mc: var(--si-m-meet); --mc-soft: var(--si-m-meet-soft); }
.m-trans { --mc: var(--si-m-trans); --mc-soft: var(--si-m-trans-soft); }
.mods { display: grid; grid-template-columns: repeat(var(--cols), minmax(0, 240px)); justify-content: center; gap: 16px; width: 100%; margin-top: 56px; }
.mod { display: flex; flex-direction: column; min-height: 208px; overflow: hidden; border: 1px solid var(--si-hair); border-radius: var(--si-radius-sheet); background: var(--si-surface-1); box-shadow: var(--si-shadow-1); color: inherit; text-decoration: none; transition: transform var(--si-d3) var(--si-ease-out), box-shadow var(--si-d3) var(--si-ease-out), border-color var(--si-d2); }
.mod:hover, .mod:focus-visible { transform: translateY(-3px); box-shadow: var(--si-shadow-2); border-color: var(--si-hair-strong); }
.illus { height: 104px; background: var(--mc-soft); }
.illus svg { display: block; width: 100%; height: 100%; }
.mod-b { display: flex; flex: 1; flex-direction: column; gap: 4px; padding: 14px 18px 16px; }
.mod-b b { font: var(--si-font-h2); color: var(--si-ink-1); }
.mod-b p { margin: 0; font: var(--si-font-body); color: var(--si-ink-3); }
.mod-st { display: flex; align-items: center; gap: 6px; min-height: 20px; margin-top: auto; padding-top: 8px; font: var(--si-font-caption); color: var(--si-ink-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mod-st.warn { color: var(--si-warn-ink); font-weight: 500; }
.mod-st i { flex: none; width: 6px; height: 6px; border-radius: 50%; background: var(--si-warn); }
.il-paper { fill: var(--si-surface-1); stroke: var(--mc); stroke-width: 1.75; stroke-opacity: .55; }
.il-line { stroke: var(--mc); stroke-width: 3; stroke-linecap: round; stroke-opacity: .35; }
.il-hl { fill: var(--mc); fill-opacity: .18; transform-box: fill-box; transform-origin: left center; transform: scaleX(.35); transition: transform 700ms var(--si-ease-out), fill-opacity var(--si-d3); }
.mod:hover .il-hl { transform: scaleX(1); fill-opacity: .28; }
.il-soft { fill: var(--mc); fill-opacity: .16; }
.il-stroke { stroke: var(--mc); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.il-paper.red, .il-line.red { transition: stroke var(--si-d3), fill var(--si-d3), stroke-opacity var(--si-d3); }
.mod:hover .il-paper.red { stroke: var(--si-danger); stroke-opacity: 1; fill: var(--si-danger-soft); }
.mod:hover .il-line.red { stroke: var(--si-danger); stroke-opacity: .6; }
.il-alert { fill: var(--si-danger); opacity: 0; transform-box: fill-box; transform-origin: center; transform: scale(.4); transition: opacity var(--si-d3), transform var(--si-d3) var(--si-ease-spring); }
.il-alert-s { stroke: var(--si-white); stroke-width: 1.75; stroke-linecap: round; opacity: 0; transition: opacity var(--si-d3); }
.mod:hover .il-alert, .mod:hover .il-alert-s { opacity: 1; transform: none; }
.il-bar { fill: var(--mc); fill-opacity: .55; transform-box: fill-box; transform-origin: center; }
.mod:hover .il-bar { animation: il-eq 900ms var(--si-ease-io) var(--d) infinite alternate; }
@keyframes il-eq { to { transform: scaleY(.4); } }
.il-line.shift { transform: translateX(8px); transition: transform 600ms var(--si-ease-out); }
.mod:hover .il-line.shift { transform: none; }
.il-link { stroke: var(--mc); stroke-width: 1.75; stroke-dasharray: 3 3; stroke-opacity: .5; }
html[data-fx="lite"] .mod:hover .il-bar { animation: none; }

/* 待你处理：一张卡，每行一个主按钮 */
.todo { width: 880px; max-width: 100%; margin-top: 56px; }
.todo-h { display: flex; align-items: center; gap: 10px; margin: 0 0 16px; font: var(--si-font-h3); color: var(--si-ink-1); }
.todo-list { overflow: hidden; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); box-shadow: var(--si-shadow-1); }
.td { display: flex; align-items: center; gap: 14px; padding: 14px 20px; border-bottom: 1px solid var(--si-hair-faint); transition: background-color var(--si-d2); }
.td:last-child { border-bottom: 0; }
.td:hover { background: var(--si-hover-bg); }
.td-ic { width: 36px; height: 36px; border-radius: 10px; }
.td-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.td-main b { font: var(--si-font-h3); font-size: calc(15px * var(--si-fs)); color: var(--si-ink-1); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.td-main span { font: var(--si-font-caption); color: var(--si-ink-3); }
.td :deep(.el-button) { margin-left: 0; }

/* 页脚信任条 */
.trust { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 14px; margin: 40px 0 0; font: var(--si-font-caption); color: var(--si-ink-3); }
.trust > span { display: inline-flex; align-items: center; gap: 6px; }
.trust svg { color: var(--si-brand-2); }
.trust svg.ai { color: var(--si-ai); }
.trust b { color: var(--si-ink-1); font-weight: 600; }
.trust > i { width: 1px; height: 12px; background: var(--si-hair-strong); }
@media (max-width: 1100px) { .mods { grid-template-columns: repeat(2, minmax(0, 240px)); } }
</style>
