<script setup lang="ts">
import { computed } from 'vue'
import type { Mark } from '@/api/runs'
import { markKindLabel } from '@/utils/labels'
import { splitMarks, unresolvedCount } from '@/utils/marks'

/** 带“未能还原”标记的纯文本（V31-U3 0.4）：原文字不变，只加虚线与浅警告底；换行方式由父级决定。 */
const props = withDefaults(defineProps<{ text: string; marks?: Mark[]; summary?: boolean }>(), { summary: false })
const segs = computed(() => splitMarks(props.text, props.marks))
const count = computed(() => props.summary ? unresolvedCount(props.marks) : 0)
</script>

<template>
  <span class="marked-text"><template v-for="(seg, i) in segs" :key="i"><mark v-if="seg.kind" class="si-unresolved" tabindex="0" :title="markKindLabel(seg.kind)" :aria-label="`${seg.text}（${markKindLabel(seg.kind)}）`">{{ seg.text }}</mark><template v-else>{{ seg.text }}</template></template><span v-if="count" class="summary">有 {{ count }} 处未能还原，已原样标出</span></span>
</template>

<style scoped>
.si-unresolved { color: inherit; background: var(--si-unresolved-bg); border-radius: 2px; text-decoration: underline dashed 1px var(--si-warn-ink); text-underline-offset: 3px; cursor: help; }
.si-unresolved:focus-visible { outline: 2px solid var(--si-warn-ink); outline-offset: 1px; }
.summary { display: block; margin-top: 4px; font: var(--si-font-caption); color: var(--si-warn-ink); white-space: normal; }
</style>
