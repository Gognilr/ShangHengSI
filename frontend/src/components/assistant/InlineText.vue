<script setup lang="ts">
import { computed } from 'vue'
import type { MarkKind } from '@/api/runs'
import type { MessageSource } from '@/api/assistant'
import { markKindLabel } from '@/utils/labels'
import { inline } from '@/utils/markdown'
/**
 * 回复中的一段行内文字：粗体、未能还原标记、依据锚点（V321-E-13）。
 * 原文段落与法条本身就是句中文字，渲染成文字链接；[KB1] 换成上标小标签。
 */
const props = defineProps<{ text: string; tokens: ReadonlyMap<string, MarkKind>; cites: ReadonlyMap<string, string>; sources: ReadonlyMap<string, MessageSource> }>()
const emit = defineEmits<{ cite: [source: MessageSource, anchor: HTMLElement] }>()
const parts = computed(() => inline(props.text, new Set(props.tokens.keys()), props.cites))
const tag = (source: MessageSource) => `知识库 ${source.ref.slice(2)}`
</script>
<template>
  <template v-for="(part, pi) in parts" :key="pi">
    <mark v-if="part.mark" class="si-unresolved" tabindex="0" :title="markKindLabel(tokens.get(part.mark)!)">{{ part.text }}</mark>
    <template v-else-if="part.cite && sources.get(part.cite)">
      <button v-if="sources.get(part.cite)!.kind === 'kb'" type="button" class="cite-tag" :title="sources.get(part.cite)!.label" @click="emit('cite', sources.get(part.cite)!, $event.currentTarget as HTMLElement)">{{ tag(sources.get(part.cite)!) }}</button>
      <button v-else type="button" class="cite-link" :class="{ bold: part.bold }" :title="sources.get(part.cite)!.kind === 'para' ? '在原文中定位' : '查看法条原文'" @click="emit('cite', sources.get(part.cite)!, $event.currentTarget as HTMLElement)">{{ part.text }}</button>
    </template>
    <strong v-else-if="part.bold">{{ part.text }}</strong>
    <span v-else>{{ part.text }}</span>
  </template>
</template>
<style scoped>
/* 未能还原（澄明 8.3）：原文字不变，琥珀色虚线下划线，悬停说明原因 */
.si-unresolved { color: inherit; background: none; border-bottom: 1.5px dashed var(--si-warn); cursor: help; }
strong, .bold { font-weight: 600; }
button { padding: 0; border: 0; background: none; font: inherit; cursor: pointer; }
.cite-link { color: var(--si-blue); text-decoration: underline dotted 1px; text-underline-offset: 3px; }
.cite-link:hover { text-decoration-style: solid; }
.cite-tag { margin: 0 2px; padding: 0 5px; vertical-align: super; font: var(--si-font-caption); line-height: 1.4; color: var(--si-blue); background: var(--el-color-primary-light-9); border-radius: var(--el-border-radius-base); }
.cite-tag:hover { background: var(--el-color-primary-light-8); }
button:focus-visible { outline: 2px solid var(--si-blue); outline-offset: 1px; }
</style>
