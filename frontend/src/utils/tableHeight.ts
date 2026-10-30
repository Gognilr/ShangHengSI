import { onBeforeUnmount, onMounted, ref } from 'vue'

/** 表格最大高度（V3-A 4.4）：视口高度减去表格以外的占用，至少 480px；超出时表体滚动、表头固定。 */
export function useTableMaxHeight(offset = 260) {
  const maxHeight = ref(480)
  const update = () => { maxHeight.value = Math.max(480, window.innerHeight - offset) }
  onMounted(() => { update(); window.addEventListener('resize', update) })
  onBeforeUnmount(() => window.removeEventListener('resize', update))
  return maxHeight
}
