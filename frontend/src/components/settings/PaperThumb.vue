<script setup lang="ts">
/**
 * 纸张缩略图（澄明二版 6.25）：范本、知识库、公司沉淀共用。角上标条款数或片段数，失败项描虚线。
 * 插槽：default 名称下的状态行（徽标、开关、操作）。点纸张触发 open。
 */
defineProps<{ name: string; count?: string; fail?: boolean; lines?: number; off?: boolean }>()
const emit = defineEmits<{ open: [] }>()
</script>

<template>
  <div class="thm" :class="{ off }">
    <button type="button" class="pg" :class="{ fail }" :aria-label="`查看 ${name}`" @click="emit('open')">
      <i class="h" /><i v-for="n in lines ?? 8" :key="n" />
      <span v-if="count" class="cn num">{{ count }}</span>
    </button>
    <b :title="name">{{ name }}</b>
    <div class="st"><slot /></div>
  </div>
</template>

<style scoped>
.thm { min-width: 0; }
.pg { position: relative; display: flex; flex-direction: column; gap: 5px; width: 100%; aspect-ratio: 1 / 1.25; box-sizing: border-box; padding: 14px 12px; border: 0; border-radius: 6px; background: var(--si-paper); box-shadow: var(--si-paper-shadow); cursor: pointer; transition: transform var(--si-d3) var(--si-ease-out); }
.thm:hover .pg { transform: translateY(-3px) rotate(-1deg); }
.pg:focus-visible { outline: 3px solid var(--si-ring); outline-offset: 2px; }
.pg i { display: block; height: 4px; border-radius: 2px; background: color-mix(in srgb, var(--si-paper-ink) 14%, transparent); }
.pg i.h { width: 60%; height: 6px; margin: 0 auto 6px; background: color-mix(in srgb, var(--si-paper-ink) 24%, transparent); }
.pg .cn { position: absolute; top: 8px; right: 8px; display: flex; align-items: center; height: 20px; padding: 0 6px; border-radius: 6px; background: var(--si-blue-tint); color: var(--si-blue-ink); font-size: 11.5px; font-weight: 600; }
.pg.fail { outline: 2px dashed var(--si-danger-line); }
.off .pg { opacity: .55; }
b { display: block; margin-top: 10px; overflow: hidden; color: var(--si-ink-1); font: var(--si-font-label); text-overflow: ellipsis; white-space: nowrap; }
.st { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 4px; color: var(--si-ink-3); font: var(--si-font-caption); }
</style>
