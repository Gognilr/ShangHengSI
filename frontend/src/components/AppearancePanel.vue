<script setup lang="ts">
import { computed } from 'vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import { useAppearanceStore, type FontSize, type FxChoice, type ThemeChoice } from '@/stores/appearance'

/** 外观控件（V31-U1 3.3，澄明 8.9）：主题三张缩略图卡，字号、动效分段控件；修改立即生效，只存本浏览器。 */
const store = useAppearanceStore()

const themes: { value: ThemeChoice; label: string }[] = [{ value: 'light', label: '浅色' }, { value: 'night', label: '深色' }, { value: 'system', label: '跟随系统' }]
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
    <div class="si-row top">
      <span class="si-row-label">主题</span>
      <div class="si-row-value">
        <div class="themes" role="radiogroup" aria-label="主题">
          <button v-for="t in themes" :key="t.value" type="button" role="radio" class="theme" :class="{ on: store.themeChoice === t.value }" :aria-checked="store.themeChoice === t.value" @click="store.themeChoice = t.value">
            <span class="thumb">
              <template v-if="t.value === 'system'"><span class="pane si-thumb-light"><i class="sd" /><i class="cd" /></span><span class="pane si-thumb-dark"><i class="sd" /><i class="cd" /></span></template>
              <span v-else class="pane" :class="t.value === 'night' ? 'si-thumb-dark' : 'si-thumb-light'"><i class="sd" /><i class="cd" /><i class="cd" /></span>
            </span>
            <span class="tl">{{ t.label }}</span>
          </button>
        </div>
      </div>
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
.top { align-items: flex-start; }
.themes { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; max-width: 480px; }
.theme { display: flex; flex-direction: column; gap: 8px; padding: 0; border: 0; background: none; color: var(--si-ink-2); font: var(--si-font-label-sm); cursor: pointer; text-align: center; }
.thumb { display: flex; height: 72px; box-sizing: border-box; overflow: hidden; border: 2px solid var(--si-hair); border-radius: var(--si-radius-md); transition: border-color var(--si-d2) var(--si-ease-out), transform var(--si-d3) var(--si-ease-out); }
.theme:hover .thumb { transform: translateY(-2px); }
.theme.on .thumb { border-color: var(--si-brand-2); box-shadow: var(--si-ring); }
.theme.on .tl { color: var(--si-blue-ink); font-weight: 600; }
.theme:focus-visible .thumb { outline: 3px solid var(--si-ring); outline-offset: 2px; }
.pane { flex: 1; display: grid; grid-template-columns: 24% 1fr; grid-template-rows: 1fr 1fr; gap: 5px; padding: 7px; background: var(--th-bg); }
.pane .sd { grid-row: 1 / 3; border-radius: 4px; background: var(--th-side); border: 1px solid var(--th-line); }
.pane .cd { border-radius: 4px; background: var(--th-card); border: 1px solid var(--th-line); border-top: 3px solid var(--th-brand); }
</style>
