<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { ApiError } from '@/api/http'
import { bidApi, bidLevelLabel, type Bidder, type BidLevel, type BidSignMark, type BidSigning } from '@/api/bid'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import BidItemCard from './BidItemCard.vue'
import { bidResultsKey } from './results'

// 各投标人（bid.md 2.4 第 3 项）：每家一份问题清单（疑似否决项 / 一般问题 / 提示）与“签章页速览”
const props = defineProps<{ bidders: Bidder[] }>()
const current = defineModel<string>({ required: true })
const ctx = inject(bidResultsKey)!
const options = computed(() => props.bidders.map(b => ({ value: b.id, label: b.name.length > 12 ? b.name.slice(0, 12) + '…' : b.name })))
const issues = computed(() => (ctx.data.value?.items ?? []).filter(i => i.kind === 'issue' && i.bidder_ids.includes(current.value)))
const groups = computed(() => (['veto', 'general', 'hint'] as BidLevel[]).map(level => ({ level, items: issues.value.filter(i => i.level === level) })).filter(g => g.items.length))
const pages = computed(() => (ctx.data.value?.signing ?? []).filter(s => s.bidder_id === current.value))
const marked = computed(() => pages.value.filter(s => s.mark).length)
const marks: { value: BidSignMark; label: string }[] = [{ value: 'stamped', label: '已盖章' }, { value: 'missing', label: '缺章' }, { value: 'unclear', label: '看不清' }]
const signError = ref('')
async function mark(s: BidSigning, value: BidSignMark) {
  const before = s.mark, next = before === value ? null : value
  s.mark = next; signError.value = ''
  try { await bidApi.sign(ctx.projectId, s, next) }
  catch (e) { s.mark = before; signError.value = e instanceof ApiError ? e.message : '标注没有保存，请重试' }
}
</script>

<template>
  <div class="bidders">
    <SiSegmented v-if="bidders.length > 1" v-model="current" :options="options" ariaLabel="投标人" />
    <section class="block">
      <h2 class="block-title">问题清单<span class="num">{{ issues.length }}</span></h2>
      <section v-for="g in groups" :key="g.level" class="group">
        <h3 class="si-overline" :class="g.level">{{ bidLevelLabel[g.level] }}<span class="num">{{ g.items.length }}</span></h3>
        <BidItemCard v-for="item in g.items" :key="item.id" :item="item" hide-who />
      </section>
      <p v-if="!groups.length" class="hint">本地检查与模型核对未发现这一家的不符合项。要求响应矩阵中的“人工核对”行与下方签章页仍需对照原件。</p>
    </section>
    <section class="block">
      <h2 class="block-title">签章页速览<span class="num">{{ marked }} / {{ pages.length }}</span><span class="aside">只看图核对，不识别印章内容；点缩略图看大图</span></h2>
      <p v-if="signError" class="error" role="alert">{{ signError }}</p>
      <div v-if="pages.length" class="sheets">
        <figure v-for="s in pages" :key="s.file_id + (s.page ?? s.loc)" class="sheet" :class="s.mark">
          <button v-if="s.renderable && s.page" type="button" class="thumb" :aria-label="`查看 ${s.title} 大图`" @click="ctx.openPage(s.file_id, s.page)">
            <img :src="bidApi.pageUrl(ctx.projectId, s.file_id, s.page, 360)" :alt="`${s.file} 第 ${s.page} 页`" loading="lazy">
          </button>
          <div v-else class="thumb word"><span>{{ s.title }}</span><small>Word 文件没有页面图，请在原件中核对</small></div>
          <figcaption>
            <span class="t">{{ s.title }}</span><span class="hint">{{ s.file }}<template v-if="s.page"> · 第 {{ s.page }} 页</template></span>
            <span class="marks" role="radiogroup" :aria-label="`${s.title} 的签章`">
              <button v-for="m in marks" :key="m.value" type="button" role="radio" :aria-checked="s.mark === m.value" :class="[m.value, { on: s.mark === m.value }]" @click="mark(s, m.value)">{{ m.label }}</button>
            </span>
          </figcaption>
        </figure>
      </div>
      <p v-else class="hint">没有在这一家的文件中找到投标函、授权委托书、报价表、承诺函等要求盖章签字的格式页；扫描页识别完成后会补上。</p>
    </section>
  </div>
</template>

<style scoped>
.bidders { display: flex; flex-direction: column; gap: 28px; }
.block { display: flex; flex-direction: column; gap: 16px; }
.block-title { display: flex; align-items: baseline; gap: 10px; margin: 0; font: var(--si-font-h2); color: var(--si-ink-1); }
.block-title .num { font: var(--si-font-code-sm); color: var(--si-ink-4); }
.aside { margin-left: auto; font: var(--si-font-meta); color: var(--si-ink-4); }
.group h3 { display: flex; align-items: center; gap: 8px; margin: 0 0 4px; padding-bottom: 8px; border-bottom: 1px solid var(--si-hair-strong); }
.group h3 .num { color: var(--si-ink-4); }
h3.veto { color: var(--si-danger-ink); }
h3.general { color: var(--si-warn-ink); }
.hint { margin: 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.error { margin: 0; color: var(--si-danger-ink); font: var(--si-font-meta); }
.sheets { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 20px; }
.sheet { margin: 0; display: flex; flex-direction: column; gap: 8px; }
.thumb { display: block; box-sizing: border-box; height: 250px; padding: 0; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-paper); overflow: hidden; cursor: zoom-in; }
.thumb img { width: 100%; height: 100%; object-fit: contain; object-position: top; display: block; }
.thumb.word { display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 8px; padding: 16px; cursor: default; text-align: center; color: var(--si-paper-ink); font: var(--si-font-h4); }
.thumb.word small { font: var(--si-font-meta); color: var(--si-paper-ink-2); }
.sheet.missing .thumb { border-color: var(--si-danger); }
figcaption { display: flex; flex-direction: column; gap: 4px; }
.t { font: var(--si-font-label); color: var(--si-ink-1); }
figcaption .hint { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.marks { display: inline-flex; margin-top: 4px; border: 1px solid var(--si-hair-strong); border-radius: 8px; overflow: hidden; align-self: flex-start; }
.marks button { padding: 3px 10px; border: 0; background: transparent; font: var(--si-font-label-sm); color: var(--si-ink-3); cursor: pointer; }
.marks button + button { border-left: 1px solid var(--si-hair); }
.marks button:hover { color: var(--si-ink-1); background: var(--si-hover-bg); }
.marks button.on { font-weight: 600; color: var(--si-ink-1); background: var(--si-surface-2); }
.marks button.missing.on { color: var(--si-danger-ink); }
.marks button.unclear.on { color: var(--si-warn-ink); }
</style>
