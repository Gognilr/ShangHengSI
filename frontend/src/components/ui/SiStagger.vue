<script setup lang="ts">
import { useFx } from '@/utils/motion'

/**
 * 依次弹入（澄明二版第四节，借 Magic UI AnimatedList 的节奏，不用模糊）：待办、新问题、新行从下方弹入，每项晚 45ms，最多 8 项。
 * 子元素要有 key，并带 data-i（序号）。完整档依次弹入；精简档只淡入；关闭档直接出现。
 */
withDefaults(defineProps<{ tag?: string }>(), { tag: 'div' })
const fx = useFx()
function delay(el: Element) {
  const i = Number((el as HTMLElement).dataset.i ?? 0)
  ;(el as HTMLElement).style.transitionDelay = fx.value === 'full' ? `${Math.min(i, 7) * 45}ms` : '0ms'
}
function clear(el: Element) { (el as HTMLElement).style.transitionDelay = '' }
</script>

<template>
  <TransitionGroup :tag="tag" :name="fx === 'full' ? 'si-list' : fx === 'lite' ? 'si-fade' : ''" appear @before-enter="delay" @after-enter="clear"><slot /></TransitionGroup>
</template>
