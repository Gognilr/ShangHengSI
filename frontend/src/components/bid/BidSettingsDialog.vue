<script setup lang="ts">
import { reactive, watch } from 'vue'
import type { BidSettings } from '@/api/bid'

// 新建项目与项目设置共用：名称、开标日期、外发策略（两张选项卡）
const props = withDefaults(defineProps<{ title: string; confirmText: string; initial?: BidSettings; busy?: boolean; error?: string }>(), { busy: false, error: '' })
const emit = defineEmits<{ submit: [body: BidSettings] }>()
const open = defineModel<boolean>({ required: true })

function today() {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date())
  return ['year', 'month', 'day'].map(k => parts.find(p => p.type === k)?.value).join('-')
}
const form = reactive<BidSettings>({ title: '', opening_date: today(), policy: 'local_only' })
watch(open, value => { if (value) Object.assign(form, props.initial ?? { title: '', opening_date: today(), policy: 'local_only' }) }, { immediate: true })
const POLICIES = [
  { value: 'local_only' as const, title: '只做本地检查', desc: '不调用模型，全部比对在本机完成。' },
  { value: 'ai' as const, title: '本机检查 + 模型核对', desc: '要求响应由模型补充核对；发送前逐段确认脱敏片段。' },
]
function submit() {
  if (!form.title.trim() || !form.opening_date || props.busy) return
  emit('submit', { ...form, title: form.title.trim() })
}
</script>

<template>
  <el-dialog v-model="open" :title="title" width="560px" :close-on-click-modal="!busy" append-to-body>
    <el-form label-position="top" @submit.prevent="submit">
      <el-form-item label="项目名称"><el-input v-model="form.title" maxlength="255" placeholder="例如：2026 年空压机采购" @keyup.enter="submit" /></el-form-item>
      <el-form-item label="开标日期"><el-date-picker v-model="form.opening_date" value-format="YYYY-MM-DD" :clearable="false" class="date" /><span class="hint date-note">用于判断有效期、证书是否过期</span></el-form-item>
      <el-form-item label="外发策略">
        <div class="policies" role="radiogroup" aria-label="外发策略">
          <button v-for="p in POLICIES" :key="p.value" type="button" role="radio" class="si-card policy" :class="{ selected: form.policy === p.value }" :aria-checked="form.policy === p.value" @click="form.policy = p.value">
            <span class="radio" aria-hidden="true" /><span><strong>{{ p.title }}</strong><small>{{ p.desc }}</small></span>
          </button>
        </div>
      </el-form-item>
    </el-form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template #footer>
      <el-button :disabled="busy" @click="open = false">取消</el-button>
      <el-button type="primary" :loading="busy" :disabled="!form.title.trim() || !form.opening_date" @click="submit">{{ confirmText }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.date { width: 200px; }
.date-note { margin-left: 12px; }
.policies { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; width: 100%; }
.policy { display: flex; align-items: flex-start; gap: 10px; padding: 14px 16px; text-align: left; cursor: pointer; color: var(--si-ink-1); font: inherit; }
.policy:hover { background: var(--si-hover-bg); border-color: var(--si-hair-strong); }
.policy.selected { border-color: var(--si-ink-3); background: var(--si-hover-bg); }
.policy strong { display: block; font: var(--si-font-h4); }
.policy small { display: block; margin-top: 4px; font: var(--si-font-meta); color: var(--si-ink-3); }
.radio { flex: none; width: 14px; height: 14px; margin-top: 3px; border: 1px solid var(--si-hair-strong); border-radius: 50%; box-sizing: border-box; }
.selected .radio { border: 4px solid var(--si-brand); }
.error { margin: 0; color: var(--si-danger-ink); font: var(--si-font-meta); }
</style>
