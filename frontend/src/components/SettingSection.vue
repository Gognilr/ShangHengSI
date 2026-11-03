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
        <h2>{{ title }}</h2>
        <p v-if="description">{{ description }}</p>
      </header>
      <div class="body"><slot /></div>
    </div>
  </section>
</template>

<style scoped>
.setting-section { container-type: inline-size; padding: 24px 0; scroll-margin-top: 64px; }
.setting-section + .setting-section { border-top: 1px solid var(--el-border-color-lighter); }
.grid { display: grid; grid-template-columns: 240px minmax(0, 1fr); gap: 32px; }
.stacked .grid { grid-template-columns: minmax(0, 1fr); gap: 16px; }
.head h2 { margin: 0; font: var(--si-font-h3); font-weight: 600; }
.head p { margin: 4px 0 0; font: var(--si-font-meta); color: var(--el-text-color-secondary); }
.body { min-width: 0; }
/* 容器较窄时改为上下排列 */
@container (max-width: 760px) {
  .grid { grid-template-columns: minmax(0, 1fr); gap: 16px; }
}
</style>
