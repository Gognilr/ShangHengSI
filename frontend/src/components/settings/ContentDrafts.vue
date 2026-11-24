<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { contentDraftsApi, type ContentDraft, type DraftKind } from '@/api/contentDrafts'
import { ApiError } from '@/api/http'
import type { TemplateRow } from '@/api/templates'
import EmptyState from '@/components/EmptyState.vue'
import { docTypeLabel, stanceLabel } from '@/utils/labels'
import { formatDateTime } from '@/utils/time'

const props = defineProps<{ kind: DraftKind; templates?: TemplateRow[] }>()
const emit = defineEmits<{ (e: 'approved'): void }>()
const rows = ref<ContentDraft[]>([])
const loading = ref(true)
const error = ref('')
const open = ref(false)
const active = ref<ContentDraft>()
const selectedId = ref('')
const saving = ref(false)
const decisionError = ref('')

const candidates = computed(() => {
  const draft = active.value
  if (!draft?.doc_type || !draft.stance) return []
  const { doc_type, stance } = draft
  return (props.templates ?? []).filter(row =>
    row.enabled && row.clause_count < 200 && row.doc_types.includes(doc_type) && row.stances.includes(stance))
})
const selected = computed(() => candidates.value.find(row => row.id === selectedId.value))

function scope(row: ContentDraft) {
  if (props.kind === 'bid') return row.category ?? '分类已移除'
  return [row.doc_type && docTypeLabel(row.doc_type), row.stance && stanceLabel(row.stance)].filter(Boolean).join(' · ')
}

async function load() {
  error.value = ''
  loading.value = true
  try { rows.value = (await contentDraftsApi.list(props.kind)).items }
  catch (e) { error.value = e instanceof ApiError ? e.message : '加载失败，请重试' }
  finally { loading.value = false }
}
function show(draft: ContentDraft) {
  active.value = draft
  selectedId.value = ''
  decisionError.value = ''
  open.value = true
}
async function decide(action: 'approve' | 'return') {
  if (!active.value || saving.value) return
  saving.value = true
  decisionError.value = ''
  try {
    await contentDraftsApi.decide(props.kind, active.value.id, action,
      props.kind === 'template' && action === 'approve' ? selectedId.value : undefined)
    ElMessage.success(action === 'return' ? '已退回草稿' : props.kind === 'template' ? '已批准，正在准备向量' : '已批准，同分类的新项目将显示这条要求')
    open.value = false
    if (action === 'approve') emit('approved')
    await load()
  } catch (e) { decisionError.value = e instanceof ApiError ? e.message : '处理失败，请重试' }
  finally { saving.value = false }
}

onMounted(load)
</script>

<template>
  <div class="drafts" :aria-busy="loading">
    <p v-if="loading" role="status" class="meta">正在加载待审批草稿</p>
    <div v-else-if="error" class="error-row" role="alert">
      <span>{{ error }}</span><el-button link type="primary" @click="load">重试</el-button>
    </div>
    <EmptyState v-else-if="!rows.length" compact title="暂无待审批草稿" />
    <ul v-else class="draft-list">
      <li v-for="row in rows" :key="row.id" class="draft-row">
        <div class="draft-main">
          <p class="meta">{{ scope(row) }} · {{ formatDateTime(row.created_at) }}</p>
          <p class="preview">{{ row.text }}</p>
        </div>
        <el-button link type="primary" @click="show(row)">审批</el-button>
      </li>
    </ul>

    <el-dialog v-model="open" title="审批草稿" width="min(640px, calc(100vw - 32px))"
      :close-on-click-modal="!saving" :close-on-press-escape="!saving" :show-close="!saving">
      <template v-if="active">
        <el-alert v-if="decisionError" :title="decisionError" type="error" :closable="false" show-icon />
        <p class="meta">{{ scope(active) }}</p>
        <p class="draft-text">{{ active.text }}</p>
        <template v-if="kind === 'template'">
          <el-form label-position="top">
            <el-form-item label="追加到公司范本">
              <el-select v-model="selectedId" placeholder="选择适用范本" :disabled="saving || !candidates.length">
                <el-option v-for="row in candidates" :key="row.id" :label="row.name" :value="row.id" />
              </el-select>
            </el-form-item>
          </el-form>
          <p v-if="selected" class="meta">沿用范本范围 · {{ selected.doc_types.map(docTypeLabel).join('、') }} · {{ selected.stances.map(stanceLabel).join('、') }}</p>
          <p v-else-if="!candidates.length" class="meta">目前没有可追加条款的适用范本。</p>
        </template>
        <p v-else class="meta">启用后，同分类的新标书项目会将此条列为人工核对要求。</p>
      </template>
      <template #footer>
        <el-button :disabled="saving" @click="open = false">取消</el-button>
        <el-button :disabled="saving" @click="decide('return')">退回</el-button>
        <el-button type="primary" :loading="saving" :disabled="kind === 'template' ? !selected : !active?.category"
          @click="decide('approve')">{{ kind === 'template' ? '批准并追加' : '批准并启用' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.draft-list { list-style: none; padding: 0; margin: 0; }
.draft-row { display: flex; align-items: start; justify-content: space-between; gap: 16px; padding: 12px 0; border-bottom: 1px solid var(--si-hair); }
.draft-main { min-width: 0; }
.meta { margin: 6px 0; color: var(--si-ink-3); font: var(--si-font-label); }
.preview { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; overflow-wrap: anywhere; margin: 0; }
.draft-text { white-space: pre-wrap; overflow-wrap: anywhere; color: var(--si-ink-1); font: var(--si-font-body); }
.error-row { display: flex; align-items: center; gap: 12px; color: var(--si-danger-ink); }
</style>
