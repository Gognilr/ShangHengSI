<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { Pencil, Plus } from '@lucide/vue'
import type { BidFile, Bidder } from '@/api/bid'
import RowMore from '@/components/RowMore.vue'
import BidFiles from './BidFiles.vue'

// 一家投标人一张对象卡：代号 · 名称（点铅笔就地改）· 文件数 · 加入文件 · 更多；整张卡可拖入文件
const props = defineProps<{ projectId: string; bidder: Bidder; files: BidFile[]; disabled: boolean; busy: boolean; rowErrors: Record<string, string> }>()
const emit = defineEmits<{ rename: [name: string]; remove: []; pick: []; drop: [event: DragEvent]; password: [fid: string, password: string]; removeFile: [fid: string] }>()
const editing = ref(false), name = ref(''), over = ref(false), input = ref<{ focus: () => void }>()
async function edit() { if (props.disabled) return; name.value = props.bidder.name; editing.value = true; await nextTick(); input.value?.focus() }
function save() {
  const value = name.value.trim()
  if (value && value !== props.bidder.name) emit('rename', value)
  editing.value = false
}
function drop(event: DragEvent) { over.value = false; emit('drop', event) }
</script>

<template>
  <section class="si-card bidder" :class="{ over }" :aria-label="`投标人 ${bidder.code}`" @dragover.prevent="over = true" @dragleave.self="over = false" @drop.prevent.stop="drop">
    <header class="head">
      <span class="code" aria-hidden="true">{{ bidder.code }}</span>
      <div v-if="editing" class="rename">
        <el-input ref="input" v-model="name" maxlength="255" :aria-label="`投标人 ${bidder.code} 名称`" @keyup.enter="save" @keyup.esc="editing = false" />
        <el-button type="primary" :disabled="!name.trim()" @click="save">保存</el-button>
        <el-button text @click="editing = false">取消</el-button>
      </div>
      <template v-else>
        <h3 class="name" :title="bidder.name">{{ bidder.name }}</h3>
        <el-button class="pencil" text :disabled="disabled" aria-label="修改名称" title="修改名称" @click="edit"><Pencil :size="14" :stroke-width="1.75" /></el-button>
      </template>
      <span class="count hint num">{{ files.length }} 份文件</span>
      <el-button :disabled="disabled" @click="emit('pick')"><Plus :size="14" :stroke-width="1.75" />加入文件</el-button>
      <RowMore :disabled="disabled">
        <el-dropdown-item :disabled="files.length > 0" @click="emit('remove')">{{ files.length ? '移除投标人（先移除文件）' : '移除投标人' }}</el-dropdown-item>
      </RowMore>
    </header>
    <BidFiles v-if="files.length" class="files" :project-id="projectId" :files="files" :disabled="disabled" :busy="busy" :row-errors="rowErrors" @remove="fid => emit('removeFile', fid)" @password="(fid, pw) => emit('password', fid, pw)" />
    <button v-else type="button" class="empty" :disabled="disabled" @click="emit('pick')">把这家的投标文件拖到这里，或点击选择</button>
  </section>
</template>

<style scoped>
.bidder { padding: 16px 20px 10px; }
.bidder.over { border-color: var(--si-brand); background: var(--si-blue-tint); }
.head { display: flex; align-items: center; gap: 10px; min-height: 32px; }
.code { flex: none; width: 28px; height: 28px; display: grid; place-items: center; border-radius: 50%; background: var(--si-surface-2); font: var(--si-font-code); color: var(--si-ink-2); }
.name { margin: 0; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-h3); color: var(--si-ink-1); }
.pencil { flex: none; color: var(--si-ink-4); }
.pencil:hover { color: var(--si-ink-1); }
.rename { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; }
.rename .el-input { max-width: 360px; }
.count { margin-left: auto; flex: none; }
.head .el-button:not(.pencil) svg { margin-right: 4px; }
.files { margin-top: 8px; }
.empty { display: block; width: 100%; margin: 12px 0 8px; padding: 16px; border: 1px dashed var(--si-hair-strong); border-radius: 8px; background: transparent; color: var(--si-ink-3); font: var(--si-font-meta); cursor: pointer; }
.empty:hover:not(:disabled) { border-style: solid; border-color: var(--si-ink-3); background: var(--si-hover-bg); }
.empty:disabled { cursor: default; }
</style>
