<script setup lang="ts">
import { useId } from 'vue'
withDefaults(defineProps<{ size?: 'nav' | 'hero'; showTagline?: boolean }>(), { size: 'nav', showTagline: false })
const gradientId = `si-logo-${useId()}`
</script>

<template>
  <span class="si-logo" :class="`si-logo--${size}`">
    <span class="mark">
      <svg viewBox="0 0 64 64" aria-hidden="true">
        <defs><linearGradient :id="gradientId" x1="0" y1="0" x2="1" y2="1"><stop stop-color="var(--el-color-primary)" /><stop offset=".55" stop-color="var(--si-brand-blue)" /><stop offset="1" stop-color="var(--si-cyan)" /></linearGradient></defs>
        <g transform="skewX(-10) translate(6 0)"><path d="M48 12 H28 a10 10 0 0 0 0 20 H36 a10 10 0 0 1 0 20 H16" fill="none" :stroke="`url(#${gradientId})`" stroke-width="9" stroke-linecap="round" stroke-linejoin="round" /></g>
        <rect x="3" y="17" width="5" height="5" rx="1.2" fill="var(--si-cyan)" opacity=".9" />
        <rect x="9" y="24" width="4" height="4" rx="1" fill="var(--si-brand-blue)" opacity=".85" />
        <rect x="2" y="28" width="3.5" height="3.5" rx=".8" fill="var(--si-cyan)" opacity=".6" />
        <path d="M34 26.5 l1.6 4 4 1.5 -4 1.5 -1.6 4 -1.6 -4 -4 -1.5 4 -1.5 z" fill="var(--el-color-white)" />
      </svg>
      <span class="wordmark">商衡<b>SI</b></span>
    </span>
    <span v-if="showTagline" class="tagline">企业安全脱敏与智能审查平台</span>
  </span>
</template>

<style scoped>
.si-logo { display: inline-flex; align-items: center; gap: 12px; white-space: nowrap; }
.mark { display: inline-flex; align-items: center; gap: 8px; }
svg { width: 28px; height: 28px; flex: none; }
.wordmark { color: var(--el-color-primary); font-size: 20px; font-weight: 700; letter-spacing: .5px; }
.wordmark b { background: var(--si-brand-gradient); background-clip: text; -webkit-background-clip: text; color: transparent; }
.tagline { border-left: 1px solid var(--el-border-color-light); padding-left: 12px; color: var(--el-text-color-secondary); font-size: 13px; }
.si-logo--hero { flex-direction: column; align-items: flex-start; }
.si-logo--hero svg { width: 76px; height: 76px; }
.si-logo--hero .wordmark { font-size: 48px; }
.si-logo--hero .tagline { border: 0; padding: 0; font-size: 16px; letter-spacing: 3px; }
@media (max-width: 1439px) { .si-logo--nav .tagline { display: none; } }
</style>
