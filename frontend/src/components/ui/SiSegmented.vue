<script setup lang="ts" generic="T extends string | number">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

/** 分段控件（V31-U1 第五节）：role="radiogroup"，←/→ 切换并选中，选中块用 transform 与宽度滑动。 */
interface Option { value: T; label: string; disabled?: boolean }
const props = withDefaults(defineProps<{ options: Option[]; size?: 'default' | 'small'; ariaLabel: string }>(), { size: 'default' })
const model = defineModel<T>({ required: true })

const root = ref<HTMLElement>()
const buttons = ref<HTMLButtonElement[]>([])
const thumb = ref({ x: 0, w: 0, ready: false })

function selectedIndex(): number { return props.options.findIndex(o => o.value === model.value) }
// 没有选中项时第一个可用项可被 Tab 聚焦
function tabbable(index: number): boolean {
  const selected = selectedIndex()
  return selected >= 0 ? index === selected : index === props.options.findIndex(o => !o.disabled)
}

function measure() {
  const button = buttons.value[selectedIndex()]
  if (!button) { thumb.value = { x: 0, w: 0, ready: false }; return }
  thumb.value = { x: button.offsetLeft, w: button.offsetWidth, ready: true }
}

function move(step: 1 | -1) {
  const count = props.options.length
  let index = selectedIndex()
  for (let i = 0; i < count; i += 1) {
    index = (index + step + count) % count
    const option = props.options[index]
    if (!option.disabled) {
      model.value = option.value
      void nextTick(() => buttons.value[index]?.focus())
      return
    }
  }
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') { event.preventDefault(); move(1) }
  else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') { event.preventDefault(); move(-1) }
}

let observer: ResizeObserver | undefined
onMounted(() => {
  measure()
  // 字体加载或容器变宽后重新量取
  observer = new ResizeObserver(measure)
  if (root.value) observer.observe(root.value)
})
onBeforeUnmount(() => observer?.disconnect())
watch(() => [model.value, props.options], () => { void nextTick(measure) }, { deep: true })
</script>

<template>
  <div ref="root" class="si-segmented" :class="`si-segmented--${size}`" role="radiogroup" :aria-label="ariaLabel" @keydown="onKeydown">
    <span class="thumb" :class="{ ready: thumb.ready }" :style="{ width: `${thumb.w}px`, transform: `translateX(${thumb.x}px)` }" aria-hidden="true" />
    <button
      v-for="(option, index) in options"
      :key="String(option.value)"
      :ref="(el) => { if (el) buttons[index] = el as HTMLButtonElement }"
      type="button"
      role="radio"
      class="option"
      :class="{ selected: option.value === model }"
      :aria-checked="option.value === model"
      :tabindex="tabbable(index) ? 0 : -1"
      :disabled="option.disabled"
      @click="model = option.value"
    >{{ option.label }}</button>
  </div>
</template>

<style scoped>
.si-segmented { --seg-h: 28px; position: relative; display: inline-flex; gap: 2px; padding: 2px; background: var(--si-surface-2); border-radius: 10px; }
.si-segmented--small { --seg-h: 24px; }
.thumb { position: absolute; top: 2px; left: 0; height: var(--seg-h); border-radius: var(--el-border-radius-base); background: var(--si-surface-pop); box-shadow: 0 0 0 1px var(--si-hair-strong), var(--si-shadow-1); opacity: 0; }
.thumb.ready { opacity: 1; transition: transform var(--si-dur-2) var(--si-spring), width var(--si-dur-2) var(--si-ease); }
.option { position: relative; height: var(--seg-h); padding: 0 14px; border: 0; border-radius: var(--el-border-radius-base); background: transparent; color: var(--si-ink-3); font: var(--si-font-label); white-space: nowrap; cursor: pointer; transition: color var(--si-dur-1) var(--si-ease); }
.si-segmented--small .option { padding: 0 10px; font: var(--si-font-label-sm); }
.option:hover:not(:disabled) { color: var(--si-ink-1); }
.option.selected { color: var(--si-ink-1); }
.option:disabled { color: var(--si-ink-5); cursor: not-allowed; }
.option:focus-visible { outline: 2px solid var(--si-focus); outline-offset: 1px; }
</style>
