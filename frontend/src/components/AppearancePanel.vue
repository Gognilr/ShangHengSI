<script setup lang="ts">
import { computed } from 'vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import { useAppearanceStore, type FxChoice, type Shell } from '@/stores/appearance'

/** 外观控件（V31-U1 3.3）：修改立即生效，只存本浏览器；用于“设置 · 个人”与外观对话框。 */
const store = useAppearanceStore()

const shellOptions: { value: Shell; label: string }[] = [{ value: 'night', label: '夜幕' }, { value: 'light', label: '浅色' }]
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
  <div class="appearance">
    <div class="row">
      <span class="label">外壳</span>
      <SiSegmented v-model="store.shell" :options="shellOptions" ariaLabel="外壳" />
    </div>
    <div class="row">
      <span class="label">特效</span>
      <div class="control">
        <SiSegmented v-model="store.fxChoice" :options="fxOptions" ariaLabel="特效" />
        <p class="hint">{{ fxHint }}</p>
      </div>
    </div>
    <p class="hint note">外观只保存在本浏览器；文件纸面跟随外壳。侧栏底部的太阳 / 月亮按钮可一键切换外壳。</p>
  </div>
</template>

<style scoped>
.row { display: flex; align-items: flex-start; gap: 16px; margin-bottom: 16px; }
.label { flex: none; width: 40px; padding-top: 5px; font: var(--si-font-label); color: var(--si-ink-2); }
.control .hint { margin: 8px 0 0; }
.note { margin: 8px 0 0; }
</style>
