<script setup lang="ts">
import { computed, inject } from 'vue'
import { bidApi, type BidSigning, type BidSignMark } from '@/api/bid'
import { bidResultsKey } from './results'

// 一家的签章页：缩略图（PDF 与图片）或一行（Word），每页 已盖章 / 缺章 / 看不清。各投标人页与逐条核对共用。
// “本家全部已盖章”只标还没标注的页，先把例外页标为缺章或看不清再点（V34Q-27 第二部分第 6 条）。
// hideAll：逐条核对右栏已有“本家全部已盖章”（Y）
const props = defineProps<{ pages: BidSigning[]; hideAll?: boolean }>()
const ctx = inject(bidResultsKey)!
const thumbs = computed(() => props.pages.filter(s => s.renderable && s.page))
const lines = computed(() => props.pages.filter(s => !(s.renderable && s.page)))
const left = computed(() => props.pages.filter(s => !s.mark))
const marks: { value: BidSignMark; label: string }[] = [{ value: 'stamped', label: '已盖章' }, { value: 'missing', label: '缺章' }, { value: 'unclear', label: '看不清' }]
const mark = (s: BidSigning, value: BidSignMark) => ctx.markSigning([s], s.mark === value ? null : value)
const allStamped = () => ctx.markSigning(left.value, 'stamped')
defineExpose({ allStamped, left })
</script>

<template>
  <div class="sign">
    <p v-if="left.length && !hideAll" class="all">
      <button type="button" class="act" @click="allStamped">本家全部已盖章</button>
      <span class="hint">把未标注的 {{ left.length }} 页标为已盖章；缺章、看不清的先单独标</span>
    </p>
    <div v-if="thumbs.length" class="sheets">
      <figure v-for="s in thumbs" :key="s.file_id + s.page" class="sheet" :class="s.mark">
        <button type="button" class="thumb" :aria-label="`查看 ${s.title} 大图`" @click="ctx.openPage(s.file_id, s.page!)">
          <img :src="bidApi.pageUrl(ctx.projectId, s.file_id, s.page!, 360)" :alt="`${s.file} 第 ${s.page} 页`" loading="lazy">
        </button>
        <figcaption>
          <span class="t">{{ s.title }}</span><span class="hint">{{ s.file }} · 第 {{ s.page }} 页</span>
          <span class="marks" role="radiogroup" :aria-label="`${s.title} 的签章`">
            <button v-for="m in marks" :key="m.value" type="button" role="radio" :aria-checked="s.mark === m.value" :class="[m.value, { on: s.mark === m.value }]" @click="mark(s, m.value)">{{ m.label }}</button>
          </span>
        </figcaption>
      </figure>
    </div>
    <ul v-if="lines.length" class="lines">
      <li v-for="s in lines" :key="s.file_id + s.loc">
        <span class="t">{{ s.title }}</span><span class="hint">{{ s.file }}<template v-if="s.page"> · 第 {{ s.page }} 页</template> · 到原件核对</span>
        <span class="marks" role="radiogroup" :aria-label="`${s.title} 的签章`">
          <button v-for="m in marks" :key="m.value" type="button" role="radio" :aria-checked="s.mark === m.value" :class="[m.value, { on: s.mark === m.value }]" @click="mark(s, m.value)">{{ m.label }}</button>
        </span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.all { display: flex; align-items: center; gap: 12px; margin: 0 0 14px; }
.act { padding: 3px 10px; border: 1px solid var(--si-hair-strong); border-radius: 8px; background: transparent; font: var(--si-font-label-sm); color: var(--si-ink-2); cursor: pointer; }
.act:hover { color: var(--si-ink-1); background: var(--si-hover-bg); }
.hint { font: var(--si-font-meta); color: var(--si-ink-3); }
.lines { margin: 0; padding: 0; list-style: none; border-top: 1px solid var(--si-hair-strong); }
.sheets { display: grid; grid-template-columns: repeat(auto-fill, minmax(176px, 1fr)); gap: 20px; margin-bottom: 16px; }
.sheet { margin: 0; display: flex; flex-direction: column; gap: 8px; }
.thumb { display: block; box-sizing: border-box; height: 232px; padding: 0; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-paper); overflow: hidden; cursor: zoom-in; }
.thumb img { width: 100%; height: 100%; object-fit: contain; object-position: top; display: block; }
.sheet.missing .thumb { border-color: var(--si-danger); }
figcaption { display: flex; flex-direction: column; gap: 4px; }
figcaption .hint { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.t { font: var(--si-font-label); color: var(--si-ink-1); }
.lines li { display: flex; align-items: center; gap: 16px; padding: 10px 0; border-bottom: 1px solid var(--si-hair-faint); }
.lines .t { min-width: 120px; }
.lines .marks { margin: 0 0 0 auto; }
.marks { display: inline-flex; margin-top: 4px; border: 1px solid var(--si-hair-strong); border-radius: 8px; overflow: hidden; align-self: flex-start; }
.marks button { padding: 3px 10px; border: 0; background: transparent; font: var(--si-font-label-sm); color: var(--si-ink-3); cursor: pointer; }
.marks button + button { border-left: 1px solid var(--si-hair); }
.marks button:hover { color: var(--si-ink-1); background: var(--si-hover-bg); }
.marks button.on { font-weight: 600; color: var(--si-ink-1); background: var(--si-surface-2); }
.marks button.missing.on { color: var(--si-danger-ink); }
.marks button.unclear.on { color: var(--si-warn-ink); }
</style>
