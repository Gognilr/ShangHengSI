<script setup lang="ts">
import { computed } from 'vue'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import SiTrip from '@/components/ui/SiTrip.vue'
import type { Trip } from '@/utils/trip'
import { useAppearanceStore, type FontSize, type FxChoice, type ThemeChoice } from '@/stores/appearance'

/** 外观控件（V31-U1 3.3，澄明 8.9）：主题三张缩略图卡，字号、动效分段控件；修改立即生效，只存本浏览器。 */
const store = useAppearanceStore()

const themes: { value: ThemeChoice; label: string }[] = [{ value: 'light', label: '浅色' }, { value: 'night', label: '深色' }, { value: 'system', label: '跟随系统' }]
const sizeOptions: { value: FontSize; label: string }[] = [{ value: 'md', label: '标准' }, { value: 'lg', label: '较大' }, { value: 'xl', label: '特大' }]
// 四档各配一段迷你往返线预览，每张卡按自己的档显示，不跟随当前全局档（二版 6.19）
const fxCards: { value: FxChoice; label: string; desc: string }[] = [
  { value: 'auto', label: '自动', desc: '按电脑性能与系统设置选择' }, { value: 'full', label: '完整', desc: '光圈呼吸、走线、依次弹入' },
  { value: 'lite', label: '精简', desc: '光圈定格，不走线' }, { value: 'off', label: '关闭', desc: '没有任何动画' },
]
const pvTrip: Trip = { mod: 'review', at: 2, state: 'run', note: '' }
const pvLevel = (v: FxChoice) => v === 'auto' ? store.fxResolved : v
const LEVEL_LABEL = { full: '完整', lite: '精简', off: '关闭' } as const

const fxHint = computed(() => {
  if (store.fxChoice !== 'auto') return '已按你的选择固定，不再自动调整'
  const level = LEVEL_LABEL[store.fxResolved]
  return store.fxReason ? `当前：${level}（${store.fxReason}）` : `当前：${level}`
})
</script>

<template>
  <div class="frm">
    <div class="fr top">
      <span class="lab" id="ap-theme">主题</span>
      <div class="fx wide">
        <div class="themes" role="radiogroup" aria-labelledby="ap-theme">
          <button v-for="t in themes" :key="t.value" type="button" role="radio" class="theme" :class="{ on: store.themeChoice === t.value }" :aria-checked="store.themeChoice === t.value" @click="store.themeChoice = t.value">
            <span class="thumb">
              <template v-if="t.value === 'system'"><span class="pane si-thumb-light"><i class="sd" /><i class="cd" /></span><span class="pane si-thumb-dark"><i class="sd" /><i class="cd" /></span></template>
              <span v-else class="pane" :class="t.value === 'night' ? 'si-thumb-dark' : 'si-thumb-light'"><i class="sd" /><i class="cd" /><i class="cd" /></span>
            </span>
            <span class="tl"><span class="radio" :class="{ on: store.themeChoice === t.value }" aria-hidden="true" />{{ t.label }}</span>
          </button>
        </div>
      </div>
    </div>
    <div class="fr">
      <span class="lab" id="ap-size">字号</span>
      <div class="fx">
        <SiSegmented v-model="store.fontSize" :options="sizeOptions" ariaLabel="字号" />
        <p class="sample">第六条　验收　设备安装调试完毕后，甲方应在 15 个工作日内组织验收。</p>
        <p class="hint">放大全站文字，界面布局不变</p>
      </div>
    </div>
    <div class="fr top">
      <span class="lab" id="ap-fx">动效</span>
      <div class="fx wide">
        <div class="fx4" role="radiogroup" aria-labelledby="ap-fx">
          <button v-for="c in fxCards" :key="c.value" type="button" role="radio" class="fc" :class="{ on: store.fxChoice === c.value }" :aria-checked="store.fxChoice === c.value" @click="store.fxChoice = c.value">
            <b>{{ c.label }}</b>
            <span class="pv" :class="pvLevel(c.value)" aria-hidden="true"><SiTrip :trip="pvTrip" /></span>
            <span class="d">{{ c.desc }}</span>
          </button>
        </div>
        <p class="hint">{{ fxHint }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.frm { display: flex; flex-direction: column; gap: 22px; }
.fr { display: grid; grid-template-columns: 140px minmax(0, 1fr); column-gap: 20px; align-items: start; }
.lab { font: var(--si-font-label); color: var(--si-ink-2); line-height: 36px; }
.fr.top .lab { line-height: 22px; padding-top: 2px; }
.fx { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.hint { margin: 0; }
.sample { margin: 4px 0 0; padding: 14px 16px; border: 1px solid var(--si-hair-faint); border-radius: var(--si-radius-card); background: var(--si-card-2); color: var(--si-ink-2); font-size: calc(15px * var(--si-fs)); line-height: 1.7; transition: font-size var(--si-d2) var(--si-ease-out); }
.themes { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.theme { display: flex; flex-direction: column; gap: 10px; padding: 10px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); color: var(--si-ink-1); font: var(--si-font-label); cursor: pointer; text-align: left; transition: box-shadow var(--si-d2) var(--si-ease-out), transform var(--si-d3) var(--si-ease-out); }
.theme:hover { transform: translateY(-2px); }
.theme.on { box-shadow: 0 0 0 2px var(--si-brand-2); }
.theme:focus-visible { outline: 3px solid var(--si-ring); outline-offset: 2px; }
.thumb { display: flex; height: 96px; box-sizing: border-box; overflow: hidden; border: 1px solid var(--si-hair-faint); border-radius: var(--si-radius-md); }
.tl { display: flex; align-items: center; gap: 8px; }
.radio { flex: none; width: 14px; height: 14px; box-sizing: border-box; border: 1.5px solid var(--si-trip-idle); border-radius: 50%; }
.radio.on { border: 4px solid var(--si-brand-2); }
.pane { flex: 1; display: grid; grid-template-columns: 24% 1fr; grid-template-rows: 1fr 1fr; gap: 5px; padding: 8px; background: var(--th-bg); }
.pane .sd { grid-row: 1 / 3; border-radius: 4px; background: var(--th-side); border: 1px solid var(--th-line); }
.pane .cd { border-radius: 4px; background: var(--th-card); border: 1px solid var(--th-line); border-top: 3px solid var(--th-brand); }
.fx4 { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.fc { display: flex; flex-direction: column; align-items: flex-start; gap: 0; min-width: 0; overflow: hidden; padding: 12px; border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); background: var(--si-surface-1); color: var(--si-ink-1); text-align: left; cursor: pointer; transition: box-shadow var(--si-d2) var(--si-ease-out); }
.fc.on { box-shadow: 0 0 0 2px var(--si-brand-2); }
.fc:focus-visible { outline: 3px solid var(--si-ring); outline-offset: 2px; }
.fc b { font: var(--si-font-label); font-weight: 600; }
.fc .d { font: var(--si-font-caption); color: var(--si-ink-3); }
.fc .pv { display: flex; align-items: center; height: 30px; margin: 8px 0; max-width: 100%; }
@keyframes fxp-breath { 0% { transform: scale(.7); opacity: .7; } 70%, 100% { transform: scale(1.7); opacity: 0; } }
.pv.full :deep(.halo.run) { animation: fxp-breath 1.8s var(--si-ease-io) infinite; opacity: .5; transform: none; }
.pv.lite :deep(.halo.run), .pv.off :deep(.halo.run) { animation: none; opacity: .45; transform: scale(1.25); }
.pv.off :deep(.halo) { display: none; }
@media (max-width: 1100px) { .fx4 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
