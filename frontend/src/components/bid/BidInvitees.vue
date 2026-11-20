<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { LocateFixed, Pencil, Plus, X } from '@lucide/vue'
import { bidInviteeSourceLabel, type BidInvitee, type BidLoc } from '@/api/bid'

/** “招标文件与条款”抽屉里的“受邀投标单位”（V34-B-01b）：本地规则识别，可改、可删、可补；改过后重新抽取不覆盖。 */
const props = defineProps<{ items: BidInvitee[]; edited: boolean; disabled: boolean }>()
const emit = defineEmits<{ save: [names: string[]]; locate: [loc: BidLoc] }>()
const adding = ref(false), draft = ref(''), editing = ref<number | null>(null), editDraft = ref('')
const addInput = ref<{ focus: () => void }>()
const needsCheck = computed(() => props.items.some(i => i.source === 'scan'))
const names = () => props.items.map(i => i.name)

async function startAdd() { adding.value = true; await nextTick(); addInput.value?.focus() }
function add() {
  const name = draft.value.trim()
  if (!name) return
  emit('save', [...names(), name]); draft.value = ''
}
function remove(index: number) { emit('save', names().filter((_, i) => i !== index)) }
function startEdit(index: number) { if (!props.disabled) { editing.value = index; editDraft.value = props.items[index]!.name } }
function saveEdit(index: number) {
  const name = editDraft.value.trim()
  editing.value = null
  if (name && name !== props.items[index]!.name) emit('save', names().map((n, i) => (i === index ? name : n)))
}
// 整段高亮
function locate(item: BidInvitee) { if (item.file_id && item.loc) emit('locate', { file_id: item.file_id, loc: item.loc, start: 0, end: 1e6 }) }
</script>

<template>
  <section class="invitees" aria-labelledby="invitees-title">
    <div class="head">
      <h3 id="invitees-title">受邀投标单位 <span class="num">{{ items.length }}</span> 家</h3>
      <span class="hint">{{ items.length ? (needsCheck ? '含扫描页识别的名称，请对照原文核对' : edited ? '已按你的修改保存' : '从招标文件中识别，请核对') : '' }}</span>
      <el-button size="small" text :disabled="disabled" @click="startAdd"><Plus :size="14" :stroke-width="1.75" />补充</el-button>
    </div>
    <p v-if="!items.length && !adding" class="hint empty">未在招标文件中识别到发标单位一览表；需要时可手工补充，投标人区会按名单列出待交文件的虚线卡。</p>
    <ul v-if="items.length" class="list">
      <li v-for="(item, index) in items" :key="item.name">
        <el-input v-if="editing === index" v-model="editDraft" size="small" maxlength="255" autofocus :aria-label="`修改 ${item.name}`" @keyup.enter="saveEdit(index)" @keyup.esc="editing = null" @blur="saveEdit(index)" />
        <template v-else>
          <span class="name" :title="item.name">{{ item.name }}</span>
          <span :class="['source', item.source]">{{ bidInviteeSourceLabel[item.source] }}</span>
          <span class="actions">
            <button v-if="item.loc" type="button" class="icon" aria-label="在原文中查看" title="在原文中查看" @click="locate(item)"><LocateFixed :size="13" :stroke-width="1.75" /></button>
            <button type="button" class="icon" :disabled="disabled" aria-label="修改名称" title="修改名称" @click="startEdit(index)"><Pencil :size="13" :stroke-width="1.75" /></button>
            <button type="button" class="icon" :disabled="disabled" :aria-label="`删除 ${item.name}`" title="删除" @click="remove(index)"><X :size="13" :stroke-width="1.75" /></button>
          </span>
        </template>
      </li>
    </ul>
    <div v-if="adding" class="add">
      <el-input ref="addInput" v-model="draft" size="small" maxlength="255" placeholder="单位全称" aria-label="补充受邀单位" :disabled="disabled" @keyup.enter="add" @keyup.esc="adding = false" />
      <el-button size="small" type="primary" :disabled="disabled || !draft.trim()" @click="add">加入</el-button>
      <el-button size="small" text @click="adding = false; draft = ''">完成</el-button>
    </div>
  </section>
</template>

<style scoped>
.invitees { margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--si-hair-faint); }
.head { display: flex; align-items: baseline; gap: 12px; }
.head h3 { margin: 0; font: var(--si-font-label); color: var(--si-ink-2); }
.head .num { color: var(--si-ink-1); }
.head .el-button { margin-left: auto; }
.head .el-button svg { margin-right: 4px; }
.empty { margin: 8px 0 0; }
.list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 24px; margin: 8px 0 0; padding: 0; list-style: none; }
.list li { display: flex; align-items: center; gap: 8px; min-height: 32px; border-bottom: 1px solid var(--si-hair-faint); }
.name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: var(--si-font-body); color: var(--si-ink-1); }
.source { flex: none; font: var(--si-font-caption); color: var(--si-ink-4); }
.source.scan { color: var(--si-warn-ink); }
.actions { display: flex; gap: 2px; margin-left: auto; opacity: 0; }
.list li:hover .actions, .list li:focus-within .actions { opacity: 1; }
.icon { display: grid; place-items: center; width: 24px; height: 24px; border: 0; border-radius: 4px; background: none; color: var(--si-ink-3); cursor: pointer; }
.icon:hover:not(:disabled) { background: var(--si-hover-bg); color: var(--si-ink-1); }
.icon:disabled { color: var(--si-ink-5); cursor: default; }
.add { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.add .el-input { max-width: 360px; }
@media (max-width: 1100px) { .list { grid-template-columns: minmax(0, 1fr); } }
</style>
