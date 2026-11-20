<script setup lang="ts">
// 设置分节（V3-A 5）：左侧标题与说明、右侧字段；stacked 时标题在上、内容占满宽度（放表格）
import { inject, onMounted, onBeforeUnmount, ref, useId } from 'vue'
import { settingSectionsKey } from '@/utils/settingSections'
const props = defineProps<{ title: string; description?: string; stacked?: boolean }>()
const registry = inject(settingSectionsKey, undefined), element = ref<HTMLElement>(), id = useId()
onMounted(() => { if (element.value) registry?.add({ id, title: props.title, element: element.value }) })
onBeforeUnmount(() => registry?.remove(id))
</script>

<template>
  <section :id="id" ref="element" class="setting-section" :class="{ stacked }">
    <div class="grid">
      <header class="head">
        <div class="head-text">
          <h2>{{ title }}</h2>
          <p v-if="description">{{ description }}</p>
        </div>
        <div v-if="$slots.actions" class="head-actions"><slot name="actions" /></div>
      </header>
      <div class="body"><slot /></div>
    </div>
  </section>
</template>

<style scoped>
/* 每个分组一张卡片：标题、一句说明、字段（澄明 8.9）；stacked 标记保留，现在所有分节都是上标题下内容 */
.setting-section { container-type: inline-size; padding: 24px; scroll-margin-top: 16px; background: var(--si-surface-1); border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); box-shadow: var(--si-shadow-1); }
.setting-section + .setting-section { margin-top: 16px; }
.grid { display: flex; flex-direction: column; gap: 20px; }
.head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.head h2 { margin: 0; font: var(--si-font-h3); font-weight: 600; }
.head p { margin: 4px 0 0; font: var(--si-font-meta); color: var(--si-ink-3); }
.head-actions { flex: none; }
.body { min-width: 0; }
</style>
