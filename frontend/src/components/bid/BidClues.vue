<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ChevronRight } from '@lucide/vue'
import { ApiError } from '@/api/http'
import { bidApi, bidLevelLabel, type Bidder, type BidCheck, type BidItem, type BidLevel } from '@/api/bid'

// 本地检查一的线索列表（B-03）。结果页的五个页签、左右对照与核对状态在 B-06 实施。
const props = defineProps<{ projectId: string; bidders: Bidder[]; files: { id: string; name: string }[]; stateKey: string }>()
const data = ref<BidCheck>(), error = ref(''), open = ref<Record<string, boolean>>({}), showNormal = ref(false)
const names = computed(() => Object.fromEntries(props.bidders.map(b => [b.id, b.name])))
const fileNames = computed(() => Object.fromEntries(props.files.map(f => [f.id, f.name])))
const levels: BidLevel[] = ['strong', 'medium', 'weak']
const grouped = computed(() => levels.map(level => ({ level, items: data.value?.items.filter(i => i.kind === 'clue' && i.level === level) ?? [] })).filter(g => g.items.length))
const normal = computed(() => data.value?.items.filter(i => i.kind === 'normal_dup') ?? [])
const typeLabel: Record<string, string> = { mixed: '混装', contact: '联系信息相同', props: '文件属性相同', image: '相同图片' }

async function load() {
  error.value = ''
  try { data.value = await bidApi.check(props.projectId) } catch (e) { error.value = e instanceof ApiError ? e.message : '初审结果读取失败' }
}
watch(() => props.stateKey, () => void load(), { immediate: true })

function who(item: BidItem) { return item.bidder_ids.map(id => names.value[id] ?? '已移除的投标人').join(' · ') }
function place(p: { file_id: string; page: number | null }) { return (fileNames.value[p.file_id] ?? '文件') + (p.page ? ` 第 ${p.page} 页` : '') }
</script>

<template>
  <div class="clues">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template v-if="data">
      <section v-for="g in grouped" :key="g.level" class="group">
        <h3 class="si-overline">{{ bidLevelLabel[g.level] }}<span class="num">{{ g.items.length }}</span></h3>
        <article v-for="item in g.items" :key="item.id" class="clue">
          <button type="button" class="clue-head" :aria-expanded="!!open[item.id]" @click="open[item.id] = !open[item.id]">
            <ChevronRight :size="14" :stroke-width="1.75" class="chev" :class="{ turned: open[item.id] }" aria-hidden="true" />
            <span class="dot" :class="g.level" aria-hidden="true" />
            <span class="title">{{ item.title }}</span>
            <span class="tag">{{ typeLabel[item.type] ?? item.type }}</span>
            <span v-if="item.article" class="tag">第四十条{{ item.article }}</span>
            <span class="who">{{ who(item) }}</span>
          </button>
          <div v-if="open[item.id]" class="detail">
            <dl v-for="(row, index) in item.rows" :key="index">
              <dt>{{ row.label }}<template v-if="row.count"> · {{ row.count }} 处</template></dt>
              <dd v-if="row.value" class="value">{{ row.value }}</dd>
              <dd v-if="row.width" class="hint num">{{ row.width }} × {{ row.height }}</dd>
              <dd v-for="(file, bidder) in row.files ?? {}" :key="'f' + bidder" class="hint">{{ names[bidder] }}：{{ file }}</dd>
              <template v-for="(places, bidder) in row.places ?? {}" :key="'p' + bidder">
                <dd v-for="(p, i) in places" :key="i"><span class="hint">{{ names[bidder] }} · {{ place(p) }}</span><span v-if="p.excerpt" class="excerpt">{{ p.excerpt }}</span></dd>
              </template>
            </dl>
            <p class="hint normal-reason">常见的正常原因：{{ item.normal }}</p>
          </div>
        </article>
      </section>
      <p v-if="!grouped.length && data.state === 'done'" class="hint">本地检查一未发现混装、联系信息、文件属性或图片方面的线索。</p>
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
.normal-reason { margin: 4px 0 0; }
.normal-row { display: flex; gap: 12px; margin: 0; padding: 10px 0 10px 22px; border-bottom: 1px solid var(--si-hair-faint); font: var(--si-font-body); color: var(--si-ink-2); }
.coverage ul { margin: 8px 0 0; padding-left: 18px; font: var(--si-font-body); color: var(--si-ink-2); }
.coverage li + li { margin-top: 4px; }
.error { color: var(--si-danger-ink); font: var(--si-font-meta); }
</style>
