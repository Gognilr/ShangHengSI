<script setup lang="ts">
import { computed } from 'vue'

/**
 * 标志「衡线」（澄明规范 3.3 方向 A，构造见 docs/design/v35/brand/README.md）。
 * 一页文件，正中一条横线伸出纸边：脱敏条、秤杆、初审找出的那一行；右上角灵紫星点是大模型。
 * 尺寸：侧栏 28、首页问候 44、登录 96；≥64px 用大稿（纸内细线），≤20px 用 16 网格小稿。
 * intro：开场动效 700ms（纸框淡入 → 横线自中间展开 → 星点弹亮），只在完整档播放，精简档直接是终态。
 * tilt：404 彩蛋（澄明 8.10），横线倾斜 6°，“这一页没找平”；完整、精简档带一次回弹，关闭档直接是终态。
 */
const props = withDefaults(defineProps<{
  size?: 'nav' | 'greet' | 'hero'
  px?: number
  showTagline?: boolean
  markOnly?: boolean
  intro?: boolean
  tilt?: boolean
}>(), { size: 'nav', showTagline: false, markOnly: false, intro: false, tilt: false })

const SIZE_PX = { nav: 28, greet: 44, hero: 96 } as const
const markPx = computed(() => props.px ?? SIZE_PX[props.size])
const variant = computed(() => markPx.value <= 20 ? 'sm' : markPx.value >= 64 ? 'lg' : 'std')
</script>

<template>
  <span class="si-logo" :class="[`si-logo--${size}`, { 'is-intro': intro, 'is-tilt': tilt }]">
    <svg v-if="variant === 'sm'" class="mark" viewBox="0 0 16 16" fill="none" :style="{ width: `${markPx}px`, height: `${markPx}px` }" :role="markOnly ? 'img' : undefined" :aria-label="markOnly ? '商衡SI' : undefined" :aria-hidden="markOnly ? undefined : 'true'">
      <path class="paper" d="M8.25 2.25H5.25a1.5 1.5 0 0 0-1.5 1.5v8.5a1.5 1.5 0 0 0 1.5 1.5h5.5a1.5 1.5 0 0 0 1.5-1.5V8" stroke-width="1.5" stroke-linecap="round" />
      <rect class="bar" x="1.5" y="6.5" width="13" height="3" rx="1.5" />
      <circle class="star" cx="12.25" cy="3.25" r="1.75" />
    </svg>
    <svg v-else class="mark" viewBox="0 0 24 24" fill="none" :style="{ width: `${markPx}px`, height: `${markPx}px` }" :role="markOnly ? 'img' : undefined" :aria-label="markOnly ? '商衡SI' : undefined" :aria-hidden="markOnly ? undefined : 'true'">
      <path class="paper" d="M12.5 2.5H8.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h7a2 2 0 0 0 2-2V10" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
      <path v-if="variant === 'lg'" class="paper faint" d="M9.5 6.5h2M9.5 17.5h5" stroke-width="2" stroke-linecap="round" />
      <rect class="bar" x="3" y="10" width="18" height="4" rx="2" />
      <path class="star" d="M17.5 1.5c.3 1.9.9 2.6 3 3-2.1.4-2.7 1.1-3 3-.3-1.9-.9-2.6-3-3 2.1-.4 2.7-1.1 3-3z" stroke-width=".8" stroke-linejoin="round" />
    </svg>
    <span v-if="!markOnly" class="wordmark">商衡<em lang="en">SI</em></span>
    <span v-if="showTagline && !markOnly" class="tagline">企业安全脱敏与智能审查平台</span>
  </span>
</template>

<style scoped>
.si-logo { display: inline-flex; align-items: center; gap: 10px; white-space: nowrap; }
.mark { flex: none; overflow: visible; }
.paper { stroke: var(--si-logo-a); }
.faint { stroke: var(--si-logo-faint); }
.bar { fill: var(--si-logo-a); }
.star { fill: var(--si-logo-star); stroke: var(--si-logo-star); }
/* 字标：商衡用标题色，SI 用衡青；品牌字号不在正文档位内 */
.wordmark { display: inline-flex; align-items: baseline; gap: 3px; color: var(--si-ink-1); font-family: var(--si-font-sans); font-size: 18px; font-weight: 600; line-height: 1; letter-spacing: .02em; }
.wordmark em { font-style: normal; color: var(--si-brand); letter-spacing: .04em; }
.tagline { padding-left: 12px; border-left: 1px solid var(--si-hair); color: var(--si-ink-3); font: var(--si-font-meta); }
.si-logo--greet .wordmark { font-size: 22px; }
.si-logo--hero { flex-direction: column; align-items: flex-start; gap: 20px; }
.si-logo--hero .wordmark { font-size: 34px; gap: 6px; }
.si-logo--hero .tagline { border: 0; padding: 0; }
@media (max-width: 1439px) { .si-logo--nav .tagline { display: none; } }

/* 开场动效（README“开场动效”）：只动 transform 与 opacity */
.bar, .star { transform-box: fill-box; transform-origin: center; }
html[data-fx="full"] .is-intro .paper { animation: logo-paper 240ms var(--si-ease-out) both; }
html[data-fx="full"] .is-intro .bar { animation: logo-bar 520ms var(--si-ease-out) 60ms both; }
html[data-fx="full"] .is-intro .star { animation: logo-star 340ms var(--si-ease-spring) 360ms both; }
@keyframes logo-paper { from { opacity: 0; } }
.is-tilt .bar { transform: rotate(-6deg); animation: logo-tilt 900ms var(--si-ease-spring) 300ms both; }
@keyframes logo-tilt { from { transform: none; } }
@keyframes logo-bar { from { transform: scaleX(0); } }
@keyframes logo-star { from { opacity: 0; transform: scale(0) rotate(-30deg); } }
</style>
