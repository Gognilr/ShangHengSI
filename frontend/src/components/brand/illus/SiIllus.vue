<script setup lang="ts">
import { computed } from 'vue'
import IllusFolder from './IllusFolder.vue'
import IllusPair from './IllusPair.vue'
import IllusSpread from './IllusSpread.vue'
import IllusStack from './IllusStack.vue'
import IllusTable from './IllusTable.vue'
import IllusVoiceprint from './IllusVoiceprint.vue'

/**
 * 模块物件（澄明二版第三节第 3 条）：审核一叠纸、合同对比两张纸、标书开标桌、纪要声纹带、翻译对开两页、发票管家票夹。
 * 入口、空状态、404 用。悬停才动：外层元素加 .si-hov，或给 on（停在悬停态）、live（声纹一直起伏）、open（票夹张开，拖入时）。
 */
export type IllusKind = 'stack' | 'pair' | 'table' | 'voiceprint' | 'spread' | 'folder'
const props = withDefaults(defineProps<{ kind: IllusKind; on?: boolean; live?: boolean; open?: boolean }>(), { on: false, live: false, open: false })
const MAP = {
  stack: [IllusStack, 'm-review'], pair: [IllusPair, 'm-compare'], table: [IllusTable, 'm-bid'],
  voiceprint: [IllusVoiceprint, 'm-meet'], spread: [IllusSpread, 'm-trans'], folder: [IllusFolder, 'm-inv'],
} as const
const item = computed(() => MAP[props.kind])
</script>

<template>
  <span class="si-illus" :class="[item[1], { on, live, open }]" aria-hidden="true"><component :is="item[0]" /></span>
</template>
