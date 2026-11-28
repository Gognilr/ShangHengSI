<script setup lang="ts">
// 问题卡片同形轮廓（澄明 8.10）：读取已有结果时占位，形状与问题卡片一致（左缘色带、标题、两行正文）；
// 静态不扫光，300ms 内返回不出现（.si-wait）
withDefaults(defineProps<{ count?: number }>(), { count: 4 })
</script>

<template>
  <div class="cs si-wait" aria-busy="true" aria-label="正在读取">
    <div v-for="n in count" :key="n" class="card"><i class="t" /><i /><i class="s" /></div>
  </div>
</template>

<style scoped>
.cs { display: flex; flex-direction: column; gap: 10px; }
.card { position: relative; display: flex; flex-direction: column; gap: 9px; padding: 14px 16px 14px 22px; border: 1px solid var(--si-hair-faint); border-radius: var(--si-radius-md); background: var(--si-surface-1); }
.card::before { content: ''; position: absolute; left: 9px; top: 12px; bottom: 12px; width: 3px; border-radius: 2px; background: var(--si-hair); }
.card i { display: block; height: 9px; border-radius: 5px; background: var(--si-hover-bg); }
.card i.t { width: 42%; height: 11px; }
.card i.s { width: 68%; }
</style>
