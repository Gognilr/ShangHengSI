<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ChevronRight } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { bidApi, bidLevelLabel, type Bidder, type BidCheck, type BidItem, type BidLevel, type BidPlace } from '@/api/bid'

// 本地检查的临时结果列表（B-03、B-04）：问题按疑似否决 / 一般 / 提示，线索按强 / 中 / 弱。
// 结果页的五个页签、核对状态与原件跳转在 B-06 实施。live 为真时（初审中）每 2 秒刷新，已完成的步骤先显示。
const props = defineProps<{ projectId: string; bidders: Bidder[]; files: { id: string; name: string }[]; stateKey: string; live?: boolean }>()
const data = ref<BidCheck>(), error = ref(''), open = ref<Record<string, boolean>>({}), showNormal = ref(false)
const names = computed(() => Object.fromEntries(props.bidders.map(b => [b.id, b.name])))
const fileNames = computed(() => Object.fromEntries(props.files.map(f => [f.id, f.name])))
const sections = computed(() => {
  const items = data.value?.items ?? []
  const pick = (kind: BidItem['kind'], levels: BidLevel[]) => levels.map(level => ({ level, items: items.filter(i => i.kind === kind && i.level === level) })).filter(g => g.items.length)
  return [{ title: '不符合项', groups: pick('issue', ['veto', 'general', 'hint']) }, { title: '串标线索', groups: pick('clue', ['strong', 'medium', 'weak']) }].filter(s => s.groups.length)
})
const normal = computed(() => data.value?.items.filter(i => i.kind === 'normal_dup') ?? [])
const typeLabel: Record<string, string> = {
  mixed: '混装', contact: '联系信息相同', props: '文件属性相同', image: '相同图片', similar: '相似段落', pricing: '报价规律',
  multi_total: '多个总价', sum_total: '合计与总价', ceiling: '超最高限价', uppercase: '大小写', row_calc: '分项计算', sum_rows: '合计计算',
  validity: '投标有效期', deposit: '保证金', cert_expired: '证书有效期', ai_instruction: '写给 AI 的指令',
  model_response: '模型判断', deviation_conflict: '偏离表与正文',
}
const sectionLabel: Record<string, string> = { tech: '技术参数章节，已降一档', price: '报价章节，已升一档' }
const stepsLeft = computed(() => props.live && data.value ? 3 - (data.value.steps?.length ?? 0) : 0)

let timer: ReturnType<typeof setTimeout> | undefined
async function load() {
  error.value = ''
  if (timer) clearTimeout(timer)
  try { data.value = await bidApi.check(props.projectId) } catch (e) { error.value = e instanceof ApiError ? e.message : '初审结果读取失败' }
  if (props.live) timer = setTimeout(() => void load(), 2000)
}
watch(() => [props.stateKey, props.live], () => void load(), { immediate: true })
onBeforeUnmount(() => { if (timer) clearTimeout(timer) })

function who(item: BidItem) { return item.bidder_ids.map(id => names.value[id] ?? '已移除的投标人').join(' · ') }
function place(p: BidPlace) { return (fileNames.value[p.file_id] ?? '文件') + (p.page ? ` 第 ${p.page} 页` : '') }
function places(list: BidPlace[]) { return [...new Set(list.map(place))].join('、') }
function dot(level: BidLevel) { return level === 'veto' || level === 'strong' ? 'strong' : level === 'general' || level === 'medium' ? 'medium' : '' }
</script>

<template>
  <div class="clues">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template v-if="data">
      <section v-for="sec in sections" :key="sec.title" class="block">
        <h2 class="block-title">{{ sec.title }}</h2>
        <section v-for="g in sec.groups" :key="g.level" class="group">
          <h3 class="si-overline">{{ bidLevelLabel[g.level] }}<span class="num">{{ g.items.length }}</span></h3>
          <article v-for="item in g.items" :key="item.id" class="clue">
            <button type="button" class="clue-head" :aria-expanded="!!open[item.id]" @click="open[item.id] = !open[item.id]">
              <ChevronRight :size="14" :stroke-width="1.75" class="chev" :class="{ turned: open[item.id] }" aria-hidden="true" />
              <span class="dot" :class="dot(g.level)" aria-hidden="true" />
              <span class="title">{{ item.title }}</span>
              <span class="tag">{{ typeLabel[item.type] ?? item.type }}</span>
              <span v-if="item.article" class="tag">{{ item.kind === 'clue' ? '第四十条' : '' }}{{ item.article }}</span>
              <span class="who">{{ who(item) }}</span>
            </button>
            <div v-if="open[item.id]" class="detail">
              <dl v-for="(row, index) in item.rows" :key="index">
                <dt>{{ row.label }}<template v-if="row.count"> · {{ row.count }} 处</template>
                  <template v-if="row.chars"> · 约 {{ row.chars }} 字 · {{ row.sentences }} 句<template v-if="row.level"> · {{ bidLevelLabel[row.level] }}</template><template v-if="row.section && sectionLabel[row.section]">（{{ sectionLabel[row.section] }}）</template></template></dt>
                <dd v-if="row.value" class="value">{{ row.value }}</dd>
                <dd v-if="row.width" class="hint num">{{ row.width }} × {{ row.height }}</dd>
                <dd v-for="(file, bidder) in row.files ?? {}" :key="'f' + bidder" class="hint">{{ names[bidder] }}：{{ file }}</dd>
                <template v-for="(list, bidder) in row.places ?? {}" :key="'p' + bidder">
                  <dd v-for="(p, i) in list" :key="i"><span class="hint">{{ names[bidder] }} · {{ place(p) }}</span><span v-if="p.excerpt" class="excerpt">{{ p.excerpt }}</span></dd>
                </template>
                <dd v-if="row.left && row.right" class="pair">
                  <div v-for="side in [row.left, row.right]" :key="side.bidder_id" class="side">
                    <span class="hint">{{ names[side.bidder_id] }} · {{ places(side.places) }}</span>
                    <span class="excerpt">{{ side.text }}</span>
                  </div>
                </dd>
                <dd v-if="row.items?.length" class="prices">
                  <table>
                    <thead><tr><th>分项</th><th class="num">{{ names[item.bidder_ids[0]] }}</th><th class="num">{{ names[item.bidder_ids[1]] }}</th></tr></thead>
                    <tbody><tr v-for="(it, i) in row.items" :key="i"><td>{{ it.name }}</td><td class="num">{{ it.left }}</td><td class="num">{{ it.right }}</td></tr></tbody>
                  </table>
                </dd>
              </dl>
              <p v-if="item.normal" class="hint normal-reason">常见的正常原因：{{ item.normal }}</p>
            </div>
          </article>
        </section>
      </section>
      <p v-if="!sections.length && data.state === 'done'" class="hint">本地检查未发现不符合项或串标线索。</p>
      <p v-if="stepsLeft > 0" class="hint">还有 {{ stepsLeft }} 项检查在进行，结果随后补上。</p>
      <section v-if="normal.length" class="group">
        <button type="button" class="si-overline toggle" :aria-expanded="showNormal" @click="showNormal = !showNormal">
          <ChevronRight :size="14" :stroke-width="1.75" class="chev" :class="{ turned: showNormal }" aria-hidden="true" />正常重复<span class="num">{{ normal.length }}</span>
        </button>
        <template v-if="showNormal">
          <p v-for="item in normal" :key="item.id" class="normal-row"><span>{{ item.title }}</span><span class="who">{{ who(item) }}</span></p>
        </template>
      </section>
      <section v-if="data.coverage.length" class="group coverage">
        <h3 class="si-overline">覆盖说明</h3>
        <ul><li v-for="note in data.coverage" :key="note">{{ note }}</li></ul>
      </section>
    </template>
  </div>
</template>

<style scoped>
.clues { display: flex; flex-direction: column; gap: 28px; }
.block { display: flex; flex-direction: column; gap: 20px; }
.block-title { margin: 0; font: var(--si-font-h2); color: var(--si-ink-1); }
.group h3, .toggle { display: flex; align-items: center; gap: 8px; margin: 0 0 4px; padding-bottom: 8px; border-bottom: 1px solid var(--si-hair-strong); }
.group h3 .num, .toggle .num { color: var(--si-ink-4); }
.toggle { width: 100%; border: 0; border-bottom: 1px solid var(--si-hair-strong); background: transparent; cursor: pointer; text-align: left; }
.clue { border-bottom: 1px solid var(--si-hair-faint); }
.clue-head { display: flex; align-items: center; gap: 10px; width: 100%; padding: 12px 0; border: 0; background: transparent; cursor: pointer; text-align: left; font: var(--si-font-body); color: var(--si-ink-1); }
.chev { flex: none; color: var(--si-ink-4); transition: transform .15s; }
.turned { transform: rotate(90deg); }
.dot { flex: none; width: 7px; height: 7px; border-radius: 50%; background: var(--si-ink-4); }
.dot.strong { background: var(--si-danger); }
.dot.medium { background: var(--si-warn); }
.title { min-width: 0; overflow-wrap: anywhere; }
.tag { flex: none; font: var(--si-font-meta); color: var(--si-ink-3); }
.who { margin-left: auto; flex: none; max-width: 40%; font: var(--si-font-meta); color: var(--si-ink-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.detail { padding: 0 0 14px 41px; }
dl { margin: 0 0 10px; }
dt { font: var(--si-font-label); color: var(--si-ink-2); }
dd { margin: 4px 0 0; font: var(--si-font-body); color: var(--si-ink-1); display: flex; flex-direction: column; gap: 2px; }
.value { font-weight: 600; }
.excerpt { color: var(--si-ink-2); overflow-wrap: anywhere; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.side { display: flex; flex-direction: column; gap: 4px; min-width: 0; padding-left: 12px; border-left: 2px solid var(--si-hair-strong); }
.prices table { border-collapse: collapse; font: var(--si-font-meta); color: var(--si-ink-2); }
.prices th, .prices td { padding: 4px 16px 4px 0; border-bottom: 1px solid var(--si-hair-faint); text-align: left; font-weight: normal; }
.prices th { color: var(--si-ink-3); }
.prices .num { text-align: right; }
.normal-reason { margin: 4px 0 0; }
.normal-row { display: flex; gap: 12px; margin: 0; padding: 10px 0 10px 22px; border-bottom: 1px solid var(--si-hair-faint); font: var(--si-font-body); color: var(--si-ink-2); }
.coverage ul { margin: 8px 0 0; padding-left: 18px; font: var(--si-font-body); color: var(--si-ink-2); }
.coverage li + li { margin-top: 4px; }
.error { color: var(--si-danger-ink); font: var(--si-font-meta); }
</style>
