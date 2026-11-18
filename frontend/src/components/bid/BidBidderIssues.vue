<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { ChevronRight } from '@lucide/vue'
import { bidApi, bidGroupLabel, bidLevelLabel, type Bidder, type BidLevel, type BidReview } from '@/api/bid'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import BidItemCard from './BidItemCard.vue'
import BidCellDrawer from './BidCellDrawer.vue'
import BidLede from './BidLede.vue'
import BidSignSheets from './BidSignSheets.vue'
import { bidResultsKey, type LedePart } from './results'

// 各投标人（V34Q-25 第 6 条）：一页列全这一家要核的东西——问题清单｜涉及的串标线索｜需人工核对的要求｜签章页，
// 总览“核对进度”的每一项都能在这里找到并就地标注。
const props = defineProps<{ bidders: Bidder[] }>()
const current = defineModel<string>({ required: true })
const ctx = inject(bidResultsKey)!
const options = computed(() => props.bidders.map(b => ({ value: b.id, label: b.name.length > 14 ? b.name.slice(0, 14) + '…' : b.name })))
const rename = ref(false), nameValue = ref(''), renameError = ref(''), renaming = ref(false)
const names = ref<Record<string, string>>({})
async function saveName() {
  if (!nameValue.value.trim() || renaming.value) return
  renaming.value = true; renameError.value = ''
  try {
    const project = await bidApi.renameBidder(ctx.projectId, current.value, nameValue.value.trim())
    names.value = Object.fromEntries(project.bidders.map(b => [b.id, b.name]))
    ctx.data.value = await bidApi.check(ctx.projectId)
    emit('renamed')
    rename.value = false
  } catch { renameError.value = '保存失败，请重试' }
  finally { renaming.value = false }
}
const emit = defineEmits<{ renamed: [] }>()
const name = computed(() => names.value[current.value] ?? ctx.names.value[current.value] ?? '')
const mine = computed(() => (ctx.data.value?.items ?? []).filter(i => i.bidder_ids.includes(current.value)))
const issues = computed(() => mine.value.filter(i => i.kind === 'issue' && i.review_status !== 'excluded'))
const clues = computed(() => mine.value.filter(i => i.kind === 'clue' && i.review_status !== 'excluded'))
const excluded = computed(() => mine.value.filter(i => i.kind !== 'normal_dup' && i.review_status === 'excluded'))
const groups = computed(() => (['veto', 'general', 'hint'] as BidLevel[]).map(level => ({ level, items: issues.value.filter(i => i.level === level) })).filter(g => g.items.length))
const showExcluded = ref(false)

// 需人工核对的要求：人工核对行，以及本家无法判断或引文待核实的格（已排除的划线保留，不计数）
const reqs = computed(() => {
  const d = ctx.data.value
  if (!d) return []
  const cells = new Map(d.cells.filter(c => c.bidder_id === current.value).map(c => [c.requirement_id, c]))
  return d.requirements.flatMap(r => {
    const c = cells.get(r.id)
    const why = r.method === 'manual' ? '人工核对' : c?.verdict === 'unclear' ? '无法判断' : c?.cite === 'unverified' ? '引文待核实' : null
    if (!why || (r.method !== 'manual' && c?.verdict === 'manual')) return []
    const review: BidReview = c ? c : { review_status: 'pending', note: null }
    return [{ id: r.id, text: r.text, group: bidGroupLabel[r.group], why, review, excluded: review.review_status === 'excluded' }]
  })
})
const openReqs = computed(() => reqs.value.filter(r => !r.excluded))
const drawer = ref<string | null>(null)

const pages = computed(() => (ctx.data.value?.signing ?? []).filter(s => s.bidder_id === current.value))
const marked = computed(() => pages.value.filter(s => s.mark).length)
const lede = computed<LedePart[]>(() => {
  const veto = issues.value.filter(i => i.level === 'veto').length, other = issues.value.length - veto
  const parts: LedePart[] = [[[name.value, 'b'], '：', veto ? [`疑似否决项 ${veto} 条`, 'd'] : '未发现疑似否决项', other ? `，其他问题 ${other} 条` : '']]
  if (clues.value.length) parts.push([`涉及 ${clues.value.length} 条串标线索`])
  if (openReqs.value.length) parts.push([`${openReqs.value.length} 项要求需人工核对`])
  if (pages.value.length > marked.value) parts.push([`${pages.value.length - marked.value} 页签章页待标注`])
  return parts
})
</script>

<template>
  <div class="bid-tab">
    <BidLede :parts="lede" />
    <div>
      <SiSegmented v-if="bidders.length > 1" v-model="current" class="seg" :options="options" ariaLabel="投标人" />
      <template v-if="rename">
        <el-input v-model="nameValue" maxlength="255" aria-label="投标人名称" @keyup.enter="saveName" @keyup.esc="rename = false" />
        <el-button size="small" :disabled="renaming" @click="saveName">保存</el-button>
        <el-button size="small" text @click="rename = false">取消</el-button>
        <p v-if="renameError" role="alert">{{ renameError }}</p>
      </template>
      <el-button v-else size="small" text @click="nameValue = name; rename = true">修改名称</el-button>
    </div>

    <section class="bid-block">
      <h3 class="bid-title">问题清单<span class="n">{{ issues.length }}</span></h3>
      <div v-for="g in groups" :key="g.level" class="group">
        <h4 :class="g.level">{{ bidLevelLabel[g.level] }}<span class="num">{{ g.items.length }}</span></h4>
        <BidItemCard v-for="item in g.items" :key="item.id" :item="item" hide-who readonly />
      </div>
      <p v-if="!groups.length" class="bid-empty">本地检查与模型核对未发现这一家的不符合项。</p>
    </section>

    <section v-if="clues.length" class="bid-block">
      <h3 class="bid-title">涉及的串标线索<span class="n">{{ clues.length }}</span></h3>
      <div class="list"><BidItemCard v-for="item in clues" :key="item.id" :item="item" readonly /></div>
    </section>

    <section class="bid-block">
      <h3 class="bid-title">需人工核对的要求<span class="n">{{ openReqs.length }}</span></h3>
      <ul v-if="reqs.length" class="reqs">
        <li v-for="r in reqs" :key="r.id" :class="{ excluded: r.excluded }">
          <button type="button" class="req" @click="drawer = r.id"><span class="meta">{{ r.group }} · {{ r.why }}</span><span class="text">{{ r.text }}</span></button>
          <button v-if="ctx.data.value?.conclusion.verify.some(e => e.requirement_id === r.id && e.bidder_ids.includes(current))" type="button" class="bid-link" @click="ctx.focus(`cell:${r.id}:${current}`)">需你核实</button>
        </li>
      </ul>
      <p v-else class="bid-empty">没有需要人工核对的要求。</p>
    </section>

    <section class="bid-block">
      <h3 class="bid-title">签章页<span class="n">{{ marked }} / {{ pages.length }}</span><span class="end">点缩略图看大图</span></h3>
      <BidSignSheets v-if="pages.length" :pages="pages" />
      <p v-if="!pages.length" class="bid-empty">没有在这一家的文件中找到投标函、授权委托书、报价表、承诺函等要求盖章签字的格式页；扫描页识别完成后会补上。</p>
    </section>

    <section v-if="excluded.length" class="bid-block">
      <button type="button" class="bid-fold" :aria-expanded="showExcluded" @click="showExcluded = !showExcluded">
        <ChevronRight :size="14" :stroke-width="1.75" class="chev" :class="{ turned: showExcluded }" aria-hidden="true" />已排除 {{ excluded.length }}
      </button>
      <div v-if="showExcluded" class="list"><BidItemCard v-for="item in excluded" :key="item.id" :item="item" hide-who readonly /></div>
    </section>
    <BidCellDrawer v-if="drawer" :requirement-id="drawer" :bidder-id="current" @close="drawer = null" />
  </div>
</template>

<style scoped>
.seg { align-self: flex-start; }
.list, .reqs { border-top: 1px solid var(--si-hair-strong); }
.group + .group { margin-top: 12px; }
.group h4 { display: flex; align-items: center; gap: 8px; margin: 0; padding: 8px 0; border-bottom: 1px solid var(--si-hair-strong); font: var(--si-font-label-sm); color: var(--si-ink-3); }
.group h4 .num { color: var(--si-ink-4); }
h4.veto { color: var(--si-danger-ink); }
h4.general { color: var(--si-warn-ink); }
.reqs { margin: 0; padding: 0; list-style: none; }
.reqs li { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 24px; padding: 10px 0; border-bottom: 1px solid var(--si-hair-faint); }
.req { display: flex; flex-direction: column; gap: 2px; min-width: 0; padding: 0; border: 0; background: transparent; text-align: left; cursor: pointer; }
.req .meta { font: var(--si-font-meta); color: var(--si-ink-3); }
.req .text { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; font: var(--si-font-body); color: var(--si-ink-1); }
.req:hover .text { text-decoration: underline; text-decoration-color: var(--si-hair-strong); text-underline-offset: 3px; }
.reqs li.excluded .text { color: var(--si-ink-4); text-decoration: line-through; }
.reqs :deep(.note) { min-width: 160px; max-width: 220px; }
.hint { font: var(--si-font-meta); color: var(--si-ink-3); }
</style>
