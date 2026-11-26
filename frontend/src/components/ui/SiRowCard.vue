<script setup lang="ts">
import { ref } from 'vue'

/**
 * 行卡（澄明二版第四节，管理列表里取代 el-table）：一行一个对象。
 * 插槽：icon 识别方块、title 名称、sub 一行辅助、status 状态徽标、data 主数据、actions 操作、detail 展开后的详情。
 * 悬停浮起；有 detail 时点行展开（actions 区的点击不展开）。名称后的徽标放不下时折到下一行，名称本身不断开。
 */
const props = withDefaults(defineProps<{ expandable?: boolean; open?: boolean }>(), { expandable: false, open: undefined })
const emit = defineEmits<{ 'update:open': [v: boolean] }>()
const inner = ref(false)
const isOpen = () => props.open ?? inner.value
function toggle() { if (!props.expandable) return; const v = !isOpen(); inner.value = v; emit('update:open', v) }
</script>

<template>
  <div class="si-rowcard" :class="{ open: isOpen(), expandable }">
    <div class="rc-m" :role="expandable ? 'button' : undefined" :tabindex="expandable ? 0 : undefined" :aria-expanded="expandable ? isOpen() : undefined" @click="toggle" @keydown.enter.self="toggle">
      <span v-if="$slots.icon" class="rc-i"><slot name="icon" /></span>
      <div class="rc-n"><b><slot name="title" /></b><span v-if="$slots.sub"><slot name="sub" /></span></div>
      <span v-if="$slots.status" class="rc-s"><slot name="status" /></span>
      <span v-if="$slots.data" class="rc-d"><slot name="data" /></span>
      <span v-if="$slots.actions" class="rc-a" @click.stop><slot name="actions" /></span>
    </div>
    <div v-if="$slots.detail && isOpen()" class="rc-x"><slot name="detail" /></div>
  </div>
</template>

<style scoped>
.si-rowcard { background: var(--si-surface-1); border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); box-shadow: var(--si-shadow-1); transition: transform var(--si-d3) var(--si-ease-out), box-shadow var(--si-d3) var(--si-ease-out); }
.si-rowcard:hover, .si-rowcard.open { box-shadow: var(--si-shadow-2); }
.si-rowcard:hover { transform: translateY(-2px); }
.rc-m { display: flex; align-items: center; gap: 16px; min-height: 72px; padding: 14px 16px; }
.expandable .rc-m { cursor: pointer; }
.rc-i { flex: none; display: flex; }
.rc-n { flex: 1; min-width: 0; }
.rc-n b { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; font: var(--si-font-h4); font-size: calc(15px * var(--si-fs)); color: var(--si-ink-1); }
.rc-n > span { display: block; overflow: hidden; font: var(--si-font-meta); color: var(--si-ink-3); white-space: nowrap; text-overflow: ellipsis; }
.rc-s, .rc-d, .rc-a { flex: none; display: flex; align-items: center; gap: 8px; }
.rc-x { padding: 16px 18px 18px 76px; border-top: 1px solid var(--si-hair-faint); }
</style>
