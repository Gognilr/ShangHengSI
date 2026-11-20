<script setup lang="ts">
import { computed } from 'vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import { useAppearanceStore, type FontSize, type FxChoice, type Shell } from '@/stores/appearance'

/** 外观控件（V31-U1 3.3）：修改立即生效，只存本浏览器；用于“设置 · 个人”与外观对话框。 */
const store = useAppearanceStore()

const shellOptions: { value: Shell; label: string }[] = [{ value: 'light', label: '浅色' }, { value: 'night', label: '深色' }]
const sizeOptions: { value: FontSize; label: string }[] = [{ value: 'md', label: '标准' }, { value: 'lg', label: '较大' }, { value: 'xl', label: '特大' }]
const fxOptions: { value: FxChoice; label: string }[] = [
  { value: 'auto', label: '自动' }, { value: 'full', label: '完整' }, { value: 'lite', label: '精简' }, { value: 'off', label: '关闭' },
]
const LEVEL_LABEL = { full: '完整', lite: '精简', off: '关闭' } as const

const fxHint = computed(() => {
  if (store.fxChoice !== 'auto') return '已按你的选择固定，不再自动调整'
  const level = LEVEL_LABEL[store.fxResolved]
  return store.fxReason ? `当前：${level}（${store.fxReason}）` : `当前：${level}`
})
</script>

<template>
  <div class="si-rows">
    <div class="si-row">
      <span class="si-row-label">主题</span>
      <div class="si-row-value"><SiSegmented v-model="store.shell" :options="shellOptions" ariaLabel="主题" /></div>
    </div>
    <div class="si-row">
      <span class="si-row-label">字号</span>
      <div class="si-row-value">
        <SiSegmented v-model="store.fontSize" :options="sizeOptions" ariaLabel="字号" />
        <p class="hint">放大全站文字，界面布局不变</p>
      </div>
    </div>
    <div class="si-row">
      <span class="si-row-label">动效</span>
      <div class="si-row-value">
        <SiSegmented v-model="store.fxChoice" :options="fxOptions" ariaLabel="动效" />
        <p class="hint">{{ fxHint }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.hint { margin: 8px 0 0; }
</style>
