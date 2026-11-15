<script setup lang="ts">
withDefaults(defineProps<{ size?: 'nav' | 'hero'; showTagline?: boolean; markOnly?: boolean }>(), { size: 'nav', showTagline: false, markOnly: false })
</script>

<template>
  <span class="si-logo" :class="`si-logo--${size}`">
    <span class="mark">
      <svg viewBox="0 0 32 32" fill="none" :aria-hidden="markOnly ? undefined : 'true'" :role="markOnly ? 'img' : undefined" :aria-label="markOnly ? '商衡SI' : undefined">
        <path d="M16 3.2C23 7.9 26.6 13.6 26.6 18.7a10.6 10.6 0 0 1-21.2 0C5.4 13.6 9 7.9 16 3.2Z" stroke="var(--si-logo-a)" :stroke-width="size === 'hero' ? 2 : 2.8" stroke-linejoin="round" />
        <template v-if="size === 'hero'">
          <path d="M12.4 9.3L16 12.3L19.6 9.3M9.4 13.6L16 19.1L22.6 13.6" stroke="var(--si-logo-a)" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
          <path d="M16 20.3l3.5 3.3-3.5 3.3-3.5-3.3Z" fill="var(--si-accent)" />
        </template>
        <path v-else d="M16 13.2l5 4.9-5 4.9-5-4.9Z" fill="var(--si-accent)" />
      </svg>
      <span v-if="!markOnly" class="wordmark">
        <span class="wordmark-name">商衡</span>
        <span class="wordmark-si" lang="en">SI</span>
      </span>
    </span>
    <span v-if="showTagline && !markOnly" class="tagline">企业安全脱敏与智能审查平台</span>
  </span>
</template>

<style scoped>
.si-logo { display: inline-flex; align-items: center; gap: 12px; white-space: nowrap; }
.mark { display: inline-flex; align-items: center; gap: 10px; }
svg { width: 24px; height: 24px; flex: none; }
.wordmark { display: inline-flex; align-items: center; gap: 10px; }
.wordmark-name { color: var(--si-ink-1); font-size: 18px; font-weight: 600; line-height: 1.2; letter-spacing: .14em; }
.wordmark-si { border-left: 1px solid var(--si-hair-strong); padding-left: 10px; color: var(--si-ink-3); font: var(--si-font-code-sm); letter-spacing: .06em; }
.tagline { border-left: 1px solid var(--si-hair); padding-left: 12px; color: var(--si-ink-3); font: var(--si-font-meta); }
.si-logo--hero { flex-direction: column; align-items: flex-start; gap: 16px; }
.si-logo--hero .mark { gap: 16px; }
.si-logo--hero svg { width: 64px; height: 64px; }
.si-logo--hero .wordmark-name { font-size: 34px; }
.si-logo--hero .wordmark-si { font-size: 16px; padding-left: 14px; }
.si-logo--hero .tagline { border: 0; padding: 0; }
@media (max-width: 1439px) { .si-logo--nav .tagline { display: none; } }
</style>
