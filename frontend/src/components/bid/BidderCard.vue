<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { Pencil, Plus } from '@lucide/vue'
import type { BidFile, Bidder, BidOpeningPriceInput } from '@/api/bid'
import RowMore from '@/components/RowMore.vue'
import BidFiles from './BidFiles.vue'
import BidOpeningInput from './BidOpeningInput.vue'

// 一家投标人一张对象卡：代号 · 名称（点铅笔就地改）· 文件数 · 加入文件 · 更多；整张卡可拖入文件
// 唱标价与材料分别锁定：检查完成后仍可在抽屉修正，仍使用原有就地录入控件。
const props = defineProps<{ projectId: string; bidder: Bidder; files: BidFile[]; disabled: boolean; busy: boolean; rowErrors: Record<string, string>; openingFailed?: string; openingDisabled?: boolean; suggest?: string; rateQuote?: boolean; flat?: boolean }>()
const emit = defineEmits<{ rename: [name: string]; remove: []; pick: []; drop: [event: DragEvent]; password: [fid: string, password: string]; removeFile: [fid: string]; opening: [body: BidOpeningPriceInput, done: () => void]; adopt: [name: string] }>()
const pricing = ref(false)
const priceLocked = computed(() => props.openingDisabled ?? props.disabled)
const editing = ref(false), name = ref(''), over = ref(false), input = ref<{ focus: () => void }>()
const titleName = computed(() => /标书|报价文件|投标文件|项目|\d+\s*[台套份]/.test(editing.value ? name.value : props.bidder.name))
async function edit() { if (props.disabled) return; name.value = props.bidder.name; editing.value = true; await nextTick(); input.value?.focus() }
function save() {
  const value = name.value.trim()
  if (value && value !== props.bidder.name) emit('rename', value)
  editing.value = false
}
function drop(event: DragEvent) { over.value = false; emit('drop', event) }
</script>

<template>
  <section class="bidder" :class="[flat ? 'flat' : 'si-card', { over }]" :aria-label="`投标人 ${bidder.code}`" @dragover.prevent="over = true" @dragleave.self="over = false" @drop.prevent.stop="drop">
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
    <p v-if="suggest && !editing" class="suggest">名单中为：<span class="full" :title="suggest">{{ suggest }}</span><el-button size="small" text :disabled="disabled" @click="emit('adopt', suggest)">改用</el-button></p>
    <p v-else-if="titleName" class="hint">请填写投标单位全称，便于混装检查与投标人名称脱敏。</p>
    <div v-if="!rateQuote" class="opening">
      <span class="label">唱标价（选填）</span>
      <BidOpeningInput v-if="pricing" :initial="bidder.opening_price" note :busy="priceLocked" :failed="openingFailed" @save="body => emit('opening', body, () => { pricing = false })" @cancel="pricing = false" />
      <template v-else>
        <span v-if="bidder.opening_price" class="num value">{{ bidder.opening_price.text }}<template v-if="bidder.opening_price.upper"> · {{ bidder.opening_price.upper }}</template><template v-if="bidder.opening_price.note"> · {{ bidder.opening_price.note }}</template></span>
        <el-button size="small" text :disabled="priceLocked" @click="pricing = true">{{ bidder.opening_price ? '修改' : '录入' }}</el-button>
      </template>
    </div>
    <BidFiles v-if="files.length" class="files" :project-id="projectId" :files="files" :disabled="disabled" :busy="busy" :row-errors="rowErrors" @remove="fid => emit('removeFile', fid)" @password="(fid, pw) => emit('password', fid, pw)" />
    <button v-else type="button" class="empty" :disabled="disabled" @click="emit('pick')">把这家的投标文件拖到这里，或点击选择</button>
  </section>
</template>

<style scoped>
.bidder { padding: 16px 20px 10px; }
/* 抽屉里（V35-F-05）：不再套一层卡片 */
.bidder.flat { padding: 0; }
.bidder.flat .name { white-space: normal; overflow-wrap: anywhere; }
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
.suggest { display: flex; align-items: center; gap: 4px; margin: 4px 0 0 38px; font: var(--si-font-meta); color: var(--si-ink-3); }
.suggest .full { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--si-ink-1); }
.opening { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; min-height: 28px; margin-top: 4px; font: var(--si-font-meta); color: var(--si-ink-3); }
.opening .value { color: var(--si-ink-1); }
.empty { display: block; width: 100%; margin: 12px 0 8px; padding: 16px; border: 1px dashed var(--si-hair-strong); border-radius: 8px; background: transparent; color: var(--si-ink-3); font: var(--si-font-meta); cursor: pointer; }
.empty:hover:not(:disabled) { border-style: solid; border-color: var(--si-ink-3); background: var(--si-hover-bg); }
.empty:disabled { cursor: default; }
</style>
