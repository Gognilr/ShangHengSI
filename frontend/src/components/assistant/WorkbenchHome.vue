<script setup lang="ts">
// 工作台首页（V34-A-04，visual.md 7.2）：欢迎语与输入框由页面通过插槽放入；这里放四格入口与“继续上次的工作”。
// 数据全取本人已有的列表接口（历史记录、会议纪要、翻译各第一页），失败时入口照常可点、只是不显示状态。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { assistantApi, type HistoryItem, type SessionItem } from '@/api/assistant'
import { bidApi } from '@/api/bid'
import { useAuthStore } from '@/stores/auth'
import { statusLabel } from '@/utils/labels'
import EntryStrip, { type Entry } from './EntryStrip.vue'

const auth = useAuthStore(), router = useRouter()
const canReview = computed(() => auth.can('use.review')), canMeeting = computed(() => auth.can('use.meeting')), canTranslate = computed(() => auth.can('use.translate'))
const history = ref<HistoryItem[]>([]), meetings = ref<SessionItem[]>([]), translations = ref<SessionItem[]>([])
const loaded = ref(false), asrEnabled = ref(false), mtEnabled = ref(false)
const bidChecking = ref<number | null>(null)

onMounted(async () => {
  const [h, m, t, b] = await Promise.allSettled([
    assistantApi.history({ page: 1 }),
    canMeeting.value ? assistantApi.list(1, '', 'meeting') : Promise.reject(new Error('skip')),
    canTranslate.value ? assistantApi.list(1, '', 'translate') : Promise.reject(new Error('skip')),
    auth.can('use.bid') ? bidApi.list() : Promise.reject(new Error('skip')),
  ])
  if (h.status === 'fulfilled') history.value = h.value.items
  if (m.status === 'fulfilled') { meetings.value = m.value.items.filter(i => i.file_count > 0 || i.title !== '新会话'); asrEnabled.value = m.value.asr_enabled }
  if (t.status === 'fulfilled') { translations.value = t.value.items.filter(i => i.file_count > 0 || i.title !== '新会话'); mtEnabled.value = t.value.mt_enabled }
  if (b.status === 'fulfilled') bidChecking.value = b.value.checking_count
  loaded.value = true
})

// 时间：今天写时刻，七天内写星期，再早写月日（北京时间）
const TZ = 'Asia/Shanghai'
const dayKey = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' })
const clock = new Intl.DateTimeFormat('zh-CN', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
const weekday = new Intl.DateTimeFormat('zh-CN', { timeZone: TZ, weekday: 'short' })
const monthDay = new Intl.DateTimeFormat('zh-CN', { timeZone: TZ, month: 'numeric', day: 'numeric' })
function when(value: string) {
  const d = new Date(value), days = Math.round((Date.parse(dayKey.format(new Date())) - Date.parse(dayKey.format(d))) / 86400000)
  if (days <= 0) return `今天 ${clock.format(d)}`
  if (days === 1) return `昨天 ${clock.format(d)}`
  return days < 7 ? weekday.format(d) : monthDay.format(d)
}

const reviews = computed(() => history.value.filter(r => r.kind === 'review' || r.run_count > 0))
const entries = computed<Entry[]>(() => {
  const list: Entry[] = []
  if (canReview.value) {
    const running = reviews.value.filter(r => r.reviewing).length
    const pending = reviews.value.reduce((sum, r) => sum + (!r.reviewing && r.latest_run_id ? r.pending_count : 0), 0)
    const status = !loaded.value ? '' : running ? `${running} 份审核中` : pending ? `${pending} 条待处理` : reviews.value[0] ? `上次：${when(reviews.value[0].last_activity_at)}` : '拖入文件即可开始'
    list.push({ key: 'review', to: '/review', title: '文件审核', desc: '合同、报价单、函件逐条初审，问题标在原文上', status })
  }
  if (auth.can('use.bid')) list.push({ key: 'bid', to: '/bid', title: '标书初审', desc: '多家投标文件的资格、符合性审查与串标线索', status: bidChecking.value === null ? '' : bidChecking.value ? `${bidChecking.value} 个项目初审中` : '新建项目即可开始' })
  if (canMeeting.value) {
    const last = meetings.value[0]
    list.push({ key: 'meeting', to: '/meeting', title: '会议纪要', desc: '录音在本机转写，整理成议题、决议与待办',
      status: !loaded.value ? '' : last ? `上次：${when(last.last_activity_at)} ${last.title}` : asrEnabled.value ? '录音只在本机转写' : '转写服务未启用' })
  }
  if (canTranslate.value) list.push({ key: 'translate', to: '/translate', title: '翻译', desc: '整份文件分段翻译，译文与原文逐段对照', status: !loaded.value ? '' : mtEnabled.value ? '本地模型 · 不外发' : '云端 · 脱敏后发送' })
  return list
})

// 继续上次的工作：工作台会话、审核、会议纪要、翻译合在一起按最近活动取 3 条
interface Row { id: string; title: string; kind: string; state: string; at: string; open: () => void }
const rows = computed<Row[]>(() => {
  const list: Row[] = history.value.map(r => {
    const reviewed = r.kind === 'review' || r.run_count > 0
    const state = r.reviewing ? '审核中' : r.kind === 'review' ? statusLabel(r.status)
      : r.run_count ? `已审核 ${r.run_count} 轮${r.latest_run_id && r.pending_count ? ` · 待处理 ${r.pending_count}` : ''}` : r.file_count ? `${r.file_count} 份文件` : '对话'
    return { id: r.id, title: r.title, kind: reviewed ? '文件审核' : '工作台', state, at: r.last_activity_at,
      open: () => void router.push(r.kind === 'session' ? { path: `/assistant/${r.id}`, query: reviewed ? { mode: 'review' } : {} }
        : r.status === 'draft' ? { name: 'review-new', query: { task: r.id } } : { name: 'review-result', params: { taskId: r.id } }) }
  })
  for (const m of meetings.value) list.push({ id: m.id, title: m.title, kind: '会议纪要', state: '', at: m.last_activity_at, open: () => void router.push(`/meeting/${m.id}`) })
  for (const t of translations.value) list.push({ id: t.id, title: t.title, kind: '翻译', state: '', at: t.last_activity_at, open: () => void router.push(`/translate/${t.id}`) })
  return list.sort((a, b) => Date.parse(b.at) - Date.parse(a.at)).slice(0, 3)
})
</script>

<template>
  <div class="workbench-home">
    <slot name="greeting" />
    <slot />
    <div class="below">
      <EntryStrip v-if="entries.length" :entries="entries" />
      <section v-if="rows.length" class="recent" aria-label="继续上次的工作">
        <div class="recent-head"><h2>继续上次的工作</h2><router-link to="/history" class="all">全部历史</router-link></div>
        <a v-for="row in rows" :key="row.id" href="#" class="list-row" @click.prevent="row.open()">
          <span class="name">{{ row.title }}</span>
          <span class="meta">{{ row.kind }}<template v-if="row.state"> · {{ row.state }}</template></span>
          <span class="at num">{{ when(row.at) }}</span>
        </a>
      </section>
    </div>
  </div>
</template>

<style scoped>
.workbench-home { width: 100%; margin: auto 0; padding: 24px 0; }
.below { max-width: 808px; margin: 0 auto; padding: 0 24px; box-sizing: border-box; }
.below > .entry-strip { margin-top: 28px; }
.recent { margin-top: 24px; }
.recent-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 4px; }
.recent h2 { margin: 0; font: var(--si-font-h4); color: var(--si-ink-2); }
.all { font: var(--si-font-caption); color: var(--si-ink-3); text-decoration: none; }
.all:hover { color: var(--si-ink-1); }
.list-row .name { flex: 1; }
.meta { flex: none; width: 200px; font: var(--si-font-meta); color: var(--si-ink-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.at { flex: none; width: 92px; text-align: right; font: var(--si-font-meta); color: var(--si-ink-3); }
</style>
