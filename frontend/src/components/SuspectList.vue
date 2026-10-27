<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Suspect, UserCategory } from '@/api/redaction'
import type { TaskFile } from '@/api/tasks'
import { MANUAL_CATEGORY_OPTIONS, suspectReasonLabel, toOptions } from '@/utils/labels'

const props = defineProps<{ suspects: Suspect[]; files: TaskFile[]; busy: boolean; selectedLoc?: string }>()
const emit = defineEmits<{
  (e: 'decide', id: number, decision: 'replace' | 'ignore', category: UserCategory | null, remember?: boolean): void
  (e: 'locate', loc: string): void
}>()

const categoryOptions = toOptions(MANUAL_CATEGORY_OPTIONS)

const pending = computed(() => props.suspects.filter((s) => s.decision === 'pending'))
const ignored = computed(() => props.suspects.filter((s) => s.decision === 'ignore'))
const showIgnored = ref<string[]>([])

/** loc 形如 F1-PG2-B3：取文件名和页码，其余位置原样显示。 */
function where(s: Suspect): string {
  if (s.loc === 'NOTE') return '本次补充说明'
  const m = /^F(\d+)-(.*)$/.exec(s.loc)
  const file = m ? props.files.find((f) => f.seq === Number(m[1])) : undefined
  const page = /^PG(\d+)/.exec(m?.[2] ?? '')
  const pos = page ? `第 ${page[1]} 页` : (m?.[2] ?? s.loc)
  return file ? `${file.original_name} ${pos}` : s.loc
}

// ---- 选择类别后替换 ----
const dlg = ref(false)
const target = ref<Suspect | null>(null)
const category = ref<UserCategory>('ORG')
const remember = ref(false)
watch(category, () => { remember.value = false })

function askReplace(s: Suspect) {
  remember.value = false
  target.value = s
  // long_digits 默认“其他”，其余默认“公司”
  category.value = ['person_name', 'person_like'].includes(s.reason) ? 'PERSON' : s.reason === 'long_digits' ? 'CUSTOM' : 'ORG'
  dlg.value = true
}

function submitReplace() {
  if (!target.value) return
  emit('decide', target.value.id, 'replace', category.value, category.value === 'CUSTOM' && remember.value)
  dlg.value = false
}
</script>

<template>
  <div>
    <el-empty v-if="!suspects.length" description="未发现疑似遗漏" :image-size="48" />

    <template v-else>
      <el-table :data="pending" size="small" class="suspect-table" empty-text="疑似遗漏已全部处理">
        <el-table-column label="位置" width="200">
          <template #default="{ row }"><el-button link type="primary" :aria-pressed="selectedLoc === row.loc" @click="emit('locate', row.loc)">{{ where(row) }}</el-button></template>
        </el-table-column>
        <el-table-column label="原因" width="170">
          <template #default="{ row }">{{ suspectReasonLabel(row.reason) }}</template>
        </el-table-column>
        <el-table-column label="上下文" min-width="260">
          <template #default="{ row }">
            <span class="ctx">{{ row.before }}<mark class="hit">{{ row.text }}</mark>{{ row.after }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" align="center">
          <template #default="{ row }">
            <el-button link type="primary" :disabled="busy" @click="askReplace(row)">替换</el-button>
            <el-button link :disabled="busy" @click="emit('decide', row.id, 'ignore', null)">忽略</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-collapse v-if="ignored.length" v-model="showIgnored" class="gap">
        <el-collapse-item :title="`已忽略（${ignored.length}）`" name="ignored">
          <el-table :data="ignored" size="small" class="suspect-table">
            <el-table-column label="位置" width="200">
              <template #default="{ row }">{{ where(row) }}</template>
            </el-table-column>
            <el-table-column label="原因" width="170">
              <template #default="{ row }">{{ suspectReasonLabel(row.reason) }}</template>
            </el-table-column>
            <el-table-column label="上下文" min-width="260">
              <template #default="{ row }">
                <span class="ctx">{{ row.before }}<mark class="hit">{{ row.text }}</mark>{{ row.after }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="130" align="center">
              <template #default="{ row }">
                <el-button link type="primary" :disabled="busy" @click="askReplace(row)">改为替换</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-collapse-item>
      </el-collapse>
    </template>

    <el-dialog class="u7-suspectlist" v-model="dlg" title="替换为哪一类" width="480px" append-to-body>
      <p class="ctx">{{ target?.text }}</p>
      <el-radio-group v-model="category">
        <el-radio v-for="o in categoryOptions" :key="o.value" :value="o.value">{{ o.label }}</el-radio>
      </el-radio-group>
      <el-checkbox v-if="category === 'CUSTOM'" v-model="remember">以后自动替换</el-checkbox>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" @click="submitReplace">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
:global(.u7-suspectlist .el-dialog__title), :global(.u7-suspectlist .el-drawer__title) { font: var(--si-font-h2); }
:global(.u7-suspectlist .el-dialog__body), :global(.u7-suspectlist .el-drawer__body) { font: var(--si-font-body); }

.suspect-table { border: 1px solid var(--el-border-color-light); border-radius: var(--si-radius-card); }
.ctx {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 13px;
}
.hit {
  background: var(--el-color-warning-light-8);
  color: inherit;
  border-radius: var(--si-radius-paper);
  padding: 0 4px;
}
p.ctx {
  margin: 0 0 12px;
}
</style>
