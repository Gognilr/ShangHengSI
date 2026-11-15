<script setup lang="ts">
withDefaults(defineProps<{ size?: 'nav' | 'hero'; showTagline?: boolean; markOnly?: boolean }>(), { size: 'nav', showTagline: false, markOnly: false })
</script>

<template>
  <span class="si-logo" :class="`si-logo--${size}`">
    <span class="mark">
      <svg viewBox="0 0 32 32" fill="none" :aria-hidden="markOnly ? undefined : 'true'" :role="markOnly ? 'img' : undefined" :aria-label="markOnly ? '商衡SI' : undefined">
        <!-- 圆冠、展开的肩部与收拢的底部构成松果轮廓；两档共用同一片暖色鳞。 -->
        <path d="M16 3.5C18.7 3.5 19.2 6.2 21.6 7.7C25 9.9 26.5 12.8 26.5 16.2C26.5 21.7 22.3 26.2 16 29C9.7 26.2 5.5 21.7 5.5 16.2C5.5 12.8 7 9.9 10.4 7.7C12.8 6.2 13.3 3.5 16 3.5Z" stroke="var(--si-logo-a)" :stroke-width="size === 'hero' ? 2 : 2.8" stroke-linejoin="round" />
        <path v-if="size === 'hero'" d="M12.5 8.8L16 11.8L19.5 8.8M9.8 12.4L16 16.2L22.2 12.4" stroke="var(--si-logo-a)" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
        <path d="M16 17.5l4.5 4-4.5 4-4.5-4Z" fill="var(--si-accent)" />
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
