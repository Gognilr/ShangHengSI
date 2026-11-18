<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import type { BidOpeningPrice, BidOpeningPriceInput } from '@/api/bid'

// 唱标价就地录入（V34-B-06e，bid-frontend-spec 第三节）：小写金额、可选的大写金额（卡片上另有备注），回车保存、Esc 取消，不弹对话框。
// 小写金额留空保存即清除录入，回到文件中识别的总价。
const props = defineProps<{ initial?: BidOpeningPrice | null; busy?: boolean; note?: boolean; failed?: string }>()
const emit = defineEmits<{ save: [body: BidOpeningPriceInput]; cancel: [] }>()
const amount = ref(props.initial?.amount.replace(/,/g, '') ?? ''), upper = ref(props.initial?.upper ?? ''), remark = ref(props.initial?.note ?? '')
const error = ref(''), first = ref<{ focus: () => void }>()
onMounted(async () => { await nextTick(); first.value?.focus() })
function save() {
  if (props.busy) return
  const value = amount.value.trim()
  if (value && !/^\d[\d,]*(\.\d+)?$/.test(value)) { error.value = '请输入金额，例如 1234567.89'; return }
  error.value = ''
  emit('save', { amount: value || null, upper: upper.value.trim() || null, ...(props.note ? { note: remark.value.trim() || null } : {}) })
}
</script>

<template>
  <span class="opening-input" @keyup.enter="save" @keyup.esc="emit('cancel')">
    <span class="fields">
      <el-input ref="first" v-model="amount" size="small" class="amount" aria-label="唱标价小写金额" placeholder="小写金额" />
      <el-input v-model="upper" size="small" class="upper" maxlength="64" aria-label="唱标价大写金额" placeholder="大写金额（选填）" />
      <el-input v-if="note" v-model="remark" size="small" class="remark" maxlength="100" aria-label="唱标价备注" placeholder="备注（选填）" />
      <el-button size="small" :disabled="busy" @click="save">保存</el-button>
      <el-button size="small" text :disabled="busy" @click="emit('cancel')">取消</el-button>
    </span>
    <small v-if="error || failed" class="error" role="alert">{{ error || failed }}</small>
  </span>
</template>

<style scoped>
.opening-input { display: inline-flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.fields { display: inline-flex; flex-wrap: wrap; justify-content: flex-end; align-items: center; gap: 6px; }
.amount { width: 128px; }
.upper { width: 168px; }
.remark { width: 160px; }
.fields .el-button + .el-button { margin-inline-start: 0; }
.error { color: var(--si-danger-ink); font: var(--si-font-caption); }
</style>
