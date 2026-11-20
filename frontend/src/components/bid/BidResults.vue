<script setup lang="ts">
import { computed, inject, ref, watch } from 'vue'
import { ChevronRight, ListChecks } from '@lucide/vue'
import type { BidConclusionEntry, BidProject } from '@/api/bid'
import BidEvidence from './BidEvidence.vue'
import BidModelCheck from './BidModelCheck.vue'
import { bidResultsKey, placeText } from './results'
import './charts/charts.css'

// 初审结果（澄明 8.5）：主从两栏。左栏问题清单——先列严重，再列“提示 n 条”（默认折叠），每行一句话、下面一行出处，
// 唯一的操作是“不是问题”（可撤销）；右栏是选中事项的证据。bidder 为只看这一家（点投标人卡片切换）。
const props = defineProps<{ project: BidProject; bidder: string; modelKey: string }>()
const emit = defineEmits<{ changed: []; clear: [] }>()
const ctx = inject(bidResultsKey)!
const c = computed(() => ctx.data.value?.conclusion)
const mine = (list: BidConclusionEntry[] = []) => props.bidder ? list.filter(e => e.bidder_ids.includes(props.bidder)) : list
const severe = computed(() => mine(c.value?.severe))
const hint = computed(() => mine(c.value?.hint))
const dismissed = computed(() => mine(c.value?.dismissed))
const showHint = ref(false), showDismissed = ref(false), busy = ref('')
const selected = ref('')
const all = computed(() => [...severe.value, ...hint.value, ...dismissed.value])
const current = computed(() => all.value.find(e => e.id === selected.value))
// 默认选第一条严重问题；没有时选第一条提示。切换投标人或结论变化后，选中项不在清单里就重新选。
watch([severe, hint, dismissed], () => {
  if (current.value) return
  const first = severe.value[0] ?? hint.value[0]
  selected.value = first?.id ?? ''
  if (first && !severe.value.length) showHint.value = true
}, { immediate: true })

const ocr = computed(() => props.project.check.ocr)
const preliminary = computed(() => ocr.value.total > ocr.value.done)
const scope = computed(() => props.bidder ? `${ctx.names.value[props.bidder] ?? ''} · 再点一次卡片看全部` : `全部 ${props.project.bidders.length} 家 · 点投标人卡片只看一家`)
function who(e: BidConclusionEntry) { return e.bidder_ids.map(id => ctx.shorts.value[id] ?? '已移除').join('、') }
function meta(e: BidConclusionEntry) {
  return [e.category === '串通' ? '串标线索' : e.category, !props.bidder || e.bidder_ids.length > 1 ? who(e) : '', placeText(e, ctx.fileNames.value, props.bidder || undefined)].filter(Boolean).join(' · ')
}
async function toggle(e: BidConclusionEntry, value: boolean) {
  busy.value = e.id
  try { await ctx.dismiss(e.id, value) } finally { busy.value = '' }
}
const notes = computed(() => [...(ctx.data.value?.coverage ?? []), '没有列出的事项，不等于投标文件没有问题。', '系统只指出问题并定位原件，不作否决或串通投标的认定。'])
</script>

<template>
  <div class="results">
    <div class="md">
      <section class="md-list si-card" aria-label="问题清单">
        <div class="md-h">
          <b>问题清单<span v-if="preliminary" class="si-badge is-info pre"><i class="d" />初步</span></b>
          <span class="scope">{{ scope }}<button v-if="bidder" type="button" class="clear" @click="emit('clear')">看全部</button></span>
          <span v-if="preliminary" class="pre-note">扫描页还在识别（{{ ocr.done }} / {{ ocr.total }} 页），识别完后结论自动更新</span>
        </div>
        <template v-if="c">
          <div class="mi-g">严重 <span class="num">{{ severe.length }}</span></div>
          <TransitionGroup tag="div" name="si-list" class="rows">
            <div v-for="e in severe" :key="e.id" class="iss" :class="{ on: selected === e.id }" role="button" tabindex="0" :aria-pressed="selected === e.id" @click="selected = e.id" @keydown.enter="selected = e.id">
              <span class="sev hi" aria-hidden="true" />
              <div class="grow"><b>{{ e.title }}</b><span>{{ meta(e) }}<i v-if="e.source === 'model'" class="si-badge is-ai mdl">模型补查</i></span></div>
              <el-button size="small" text class="nb" :disabled="busy === e.id" @click.stop="toggle(e, true)">不是问题</el-button>
            </div>
          </TransitionGroup>
          <p v-if="!severe.length" class="none">{{ bidder ? '这一家没有严重问题。' : '没有严重问题。' }}</p>

          <template v-if="hint.length">
            <button type="button" class="mi-g fold" :aria-expanded="showHint" @click="showHint = !showHint"><ChevronRight :size="14" :stroke-width="1.75" :class="{ turned: showHint }" />提示 <span class="num">{{ hint.length }}</span> 条</button>
            <div v-if="showHint" class="rows">
              <div v-for="e in hint" :key="e.id" class="iss" :class="{ on: selected === e.id }" role="button" tabindex="0" :aria-pressed="selected === e.id" @click="selected = e.id" @keydown.enter="selected = e.id">
                <span class="sev neu" aria-hidden="true" />
                <div class="grow"><b class="h">{{ e.title }}</b><span>{{ meta(e) }}<i v-if="e.source === 'model'" class="si-badge is-ai mdl">模型补查</i></span></div>
                <el-button size="small" text class="nb" :disabled="busy === e.id" @click.stop="toggle(e, true)">不是问题</el-button>
              </div>
            </div>
          </template>
          <template v-if="dismissed.length">
            <button type="button" class="mi-g fold" :aria-expanded="showDismissed" @click="showDismissed = !showDismissed"><ChevronRight :size="14" :stroke-width="1.75" :class="{ turned: showDismissed }" />标了“不是问题” <span class="num">{{ dismissed.length }}</span> 条</button>
            <div v-if="showDismissed" class="rows">
              <div v-for="e in dismissed" :key="e.id" class="iss off" :class="{ on: selected === e.id }" role="button" tabindex="0" @click="selected = e.id" @keydown.enter="selected = e.id">
                <span class="sev neu" aria-hidden="true" />
                <div class="grow"><b class="h">{{ e.title }}</b><span>{{ meta(e) }}</span></div>
                <el-button size="small" text class="nb shown" :disabled="busy === e.id" @click.stop="toggle(e, false)">撤销</el-button>
              </div>
            </div>
          </template>
          <BidModelCheck v-if="project.policy === 'ai'" :project-id="project.id" :state-key="modelKey" :lookup="c.lookup ?? 0" @changed="emit('changed')" />
        </template>
        <p v-else class="none" role="status">正在读取结果…</p>
      </section>

      <BidEvidence v-if="current && ctx.data.value" :key="current.id" :entry="current" />
      <section v-else class="ev-empty si-card">
        <span class="ring"><ListChecks :size="22" :stroke-width="1.75" /></span>
        <b>{{ c ? (bidder ? '这一家没有列出问题' : '没有列出问题') : '正在读取…' }}</b>
        <p class="hint">没有列出的事项，不等于投标文件没有问题；可以在投标人卡片里看签章页。</p>
      </section>
    </div>
    <ul class="foot"><li v-for="n in notes" :key="n">{{ n }}</li></ul>
  </div>
</template>

<style scoped>
.results { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.md { display: grid; grid-template-columns: 440px minmax(0, 1fr); gap: 16px; align-items: start; }
.md-list { padding: 8px 8px 12px; }
.md-h { display: flex; flex-direction: column; gap: 2px; padding: 10px 12px 6px; }
.md-h b { display: flex; align-items: center; gap: 8px; font: var(--si-font-h2); color: var(--si-ink-1); }
.pre { height: 22px; }
.scope { font: var(--si-font-caption); color: var(--si-ink-3); }
.clear { margin-left: 8px; padding: 0; border: 0; background: none; font: inherit; color: var(--si-blue-ink); cursor: pointer; }
.pre-note { font: var(--si-font-caption); color: var(--si-info-ink); }
.mi-g { display: flex; align-items: center; gap: 6px; padding: 12px 12px 6px; font: var(--si-font-label-sm); color: var(--si-ink-3); }
.mi-g.fold { width: 100%; border: 0; background: none; cursor: pointer; text-align: left; }
.mi-g.fold:hover { color: var(--si-ink-1); }
.mi-g svg { transition: transform var(--si-d2) var(--si-ease-out); }
.mi-g .turned { transform: rotate(90deg); }
.rows { position: relative; display: flex; flex-direction: column; gap: 2px; }
.iss { display: flex; align-items: flex-start; gap: 12px; padding: 12px; border-radius: 12px; cursor: pointer; transition: background-color var(--si-d2) var(--si-ease-out); }
.iss:hover { background: var(--si-hover-bg); }
.iss.on { background: var(--si-select-bg); }
.iss:focus-visible { outline: 2px solid var(--si-focus); outline-offset: -2px; }
.iss .grow { flex: 1; min-width: 0; }
.iss b { display: block; font: var(--si-font-body); font-weight: 600; color: var(--si-ink-1); overflow-wrap: anywhere; }
.iss b.h { font-weight: 500; color: var(--si-ink-2); }
.iss.off b { color: var(--si-ink-4); text-decoration: line-through; }
.iss .grow > span { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 2px; font: var(--si-font-caption); color: var(--si-ink-3); overflow-wrap: anywhere; }
.mdl { height: 20px; padding: 0 8px; font-style: normal; }
.sev { flex: none; width: 8px; height: 8px; margin-top: 8px; border-radius: 50%; }
.sev.hi { background: var(--si-danger); box-shadow: 0 0 0 3px var(--si-danger-soft); }
.sev.neu { background: var(--si-ink-5); }
.nb { flex: none; opacity: 0; transition: opacity var(--si-d2) var(--si-ease-out); }
.iss:hover .nb, .iss.on .nb, .iss:focus-within .nb, .nb.shown { opacity: 1; }
.none { margin: 4px 12px 8px; font: var(--si-font-meta); color: var(--si-ink-4); }
.ev-empty { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 48px 24px; text-align: center; }
.ring { display: grid; place-items: center; width: 52px; height: 52px; margin-bottom: 6px; border-radius: 50%; background: var(--si-blue-tint); color: var(--si-brand-2); }
.ev-empty b { font: var(--si-font-h3); color: var(--si-ink-1); }
.ev-empty p { margin: 0; max-width: 420px; }
.foot { margin: 0; padding: 12px 2px 0; border-top: 1px solid var(--si-hair); list-style: none; display: flex; flex-direction: column; gap: 4px; font: var(--si-font-caption); color: var(--si-ink-4); }
@media (max-width: 1320px) { .md { grid-template-columns: 400px minmax(0, 1fr); } }
</style>
