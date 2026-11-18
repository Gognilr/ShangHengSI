<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { BidReviewReason } from '@/api/bid'

const props = defineProps<{ label: string; triggered?: boolean; disabled?: boolean }>()
const emit = defineEmits<{ decide: [reason: BidReviewReason, detail: string, remember: boolean]; closed: [] }>()
const options: [BidReviewReason, string][] = [
  ['template', '招标范本或标准规范'], ['manufacturer', '同一厂家资料'],
  ['recognition', '识别错误'], ['model', '模型判断有误'], ['other', '其他'],
]
const visible = ref(false), reason = ref<BidReviewReason>(), detail = ref(''), remember = ref(false)
const reusable = computed(() => ['template', 'manufacturer', 'recognition'].includes(reason.value ?? ''))
watch(() => props.triggered, value => { visible.value = !!value })
watch(visible, value => {
  if (value) return
  reason.value = undefined
  detail.value = ''
  remember.value = false
  emit('closed')
})
function submit() {
  if (!reason.value) return
  emit('decide', reason.value, detail.value.trim(), reusable.value && remember.value)
  visible.value = false
}
</script>

<template>
  <el-popover v-model:visible="visible" trigger="click" placement="bottom-end" :width="275">
    <template #reference><el-button size="small" :text="label === '有异议'" :disabled="disabled">{{ label }}</el-button></template>
    <el-radio-group v-model="reason" class="reason-options" aria-label="核验原因">
      <el-radio v-for="[key, title] in options" :key="key" :value="key">{{ title }}</el-radio>
    </el-radio-group>
    <el-input v-if="reason === 'other'" v-model="detail" placeholder="填写原因" :maxlength="50" aria-label="其他原因" />
    <el-checkbox v-model="remember" :disabled="!reusable">以后同类不再报（仅本人）</el-checkbox>
    <div class="reason-actions"><el-button size="small" :disabled="!reason || (reason === 'other' && !detail.trim())" @click="submit">确定</el-button></div>
  </el-popover>
</template>

<style scoped>
.reason-options { display: flex; flex-direction: column; align-items: flex-start; }
.reason-actions { display: flex; justify-content: flex-end; }
</style>
