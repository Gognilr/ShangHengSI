<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ApiError } from '@/api/http'
import { requirementsApi, type RequirementsView } from '@/api/requirements'
import type { Source, Stance } from '@/api/tasks'
import { docTypeLabel, sourceLabel, stanceLabel } from '@/utils/labels'

/**
 * 审核要求抽屉（SPEC 6.5，设计 docs/design/M5.md 第九节）：内置要求、公司补充要求、本次补充说明三组，只读。
 * docType 为空表示文件类型未定，按全部类型显示，内置要求每条后标注适用的文件类型。
 */
const props = defineProps<{
  modelValue: boolean
  stance: Stance
  source: Source
  docType?: string | null
  userNote?: string | null
}>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const data = ref<RequirementsView | null>(null)
const loading = ref(false)
const errorText = ref('')

const ALL_TYPES = '所有文件类型'
function typesText(types: string[]): string {
  return types.includes('all') ? ALL_TYPES : types.map(docTypeLabel).join('、')
}
function scopeText(docTypes: string[], stances: string[]): string {
  const stance = stances.includes('all') ? '所有立场' : stances.map(stanceLabel).join('、')
  return `${typesText(docTypes)} · ${stance}`
}

let seq = 0
async function load() {
  const mine = ++seq
  loading.value = true
  errorText.value = ''
  try {
    const res = await requirementsApi.get({ stance: props.stance, source: props.source, docType: props.docType })
    if (mine === seq) data.value = res
  } catch (e) {
    if (mine === seq) errorText.value = e instanceof ApiError ? e.message : '加载失败，请稍后重试'
  } finally {
    if (mine === seq) loading.value = false
  }
}

// 每次打开都重新读取，保证显示的是当前生效的版本
watch(
  () => props.modelValue,
  (open) => {
    if (open) void load()
  },
)

const scopeLine = computed(
  () =>
    `${props.docType ? docTypeLabel(props.docType) : '全部文件类型'} · ${stanceLabel(props.stance)} · ${sourceLabel(props.source)}`,
)
</script>

<template>
  <el-drawer v-model="visible" title="审核要求" direction="rtl" :size="560">
    <div v-loading="loading" class="body">
      <p class="intro">AI 将按以下要求逐条审核。公司补充要求只能增加审核内容，不能取消内置要求。</p>
      <p class="scope">当前范围：{{ scopeLine }}</p>
      <el-alert v-if="errorText" type="error" show-icon :closable="false" :title="errorText" class="gap" />

      <template v-if="data">
        <h4>内置要求<span class="ver">（版本 {{ data.builtin_version }}）</span></h4>
        <p v-if="!data.builtin.length" class="empty">暂无适用的内置要求</p>
        <ul v-else class="list">
          <li v-for="r in data.builtin" :key="r.id">
            <span class="code">{{ r.id }}</span>
            <el-tag v-if="r.dimension" size="small" type="info" class="dim">{{ r.dimension }}</el-tag>
            <span>{{ r.text }}</span>
            <span v-if="!docType" class="hint">（适用：{{ typesText(r.doc_types) }}）</span>
          </li>
        </ul>

        <h4>
          公司补充要求<span v-if="data.company_version !== null" class="ver">（版本 {{ data.company_version }}）</span>
        </h4>
        <p v-if="!data.company.length" class="empty">暂无公司补充要求</p>
        <ul v-else class="list">
          <li v-for="r in data.company" :key="r.code">
            <span class="code">{{ r.code }}</span>
            <span>{{ r.text }}</span>
            <span class="hint">（{{ scopeText(r.doc_types, r.stances) }}）</span>
          </li>
        </ul>
      </template>

      <h4>本次补充说明</h4>
      <p v-if="userNote?.trim()" class="note">{{ userNote.trim() }}</p>
      <p v-else class="empty">未填写</p>
    </div>
  </el-drawer>
</template>

<style scoped>
.body {
  min-height: 120px;
}
.intro {
  margin: 0 0 4px;
}
.scope {
  margin: 0 0 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
h4 {
  margin: 20px 0 8px;
}
.ver,
.hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  font-weight: normal;
}
.hint {
  font-size: 12px;
}
.list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.list li {
  padding: 6px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
  line-height: 1.6;
}
.code {
  margin-right: 8px;
  font-family: monospace;
  font-weight: 600;
}
.dim {
  margin-right: 8px;
}
.empty {
  margin: 0;
  color: var(--el-text-color-secondary);
}
.note {
  margin: 0;
  white-space: pre-wrap;
}
.gap {
  margin-bottom: 12px;
}
</style>
