<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { adminApi, type RedactionStats } from '@/api/admin'
import { ApiError } from '@/api/http'
import SiSegmented from '@/components/ui/SiSegmented.vue'
const days = ref(30), data = ref<RedactionStats | null>(null), loading = ref(false), error = ref('')
let version=0
watch(days,async value=> { const current=++version; loading.value=true; error.value=''; try {const next=await adminApi.redactionStats(value);if(current===version)data.value=next} catch(e) {if(current===version)error.value=e instanceof ApiError?e.message:'统计加载失败，请重试'} finally {if(current===version)loading.value=false} },{immediate:true})
const fields = [{key:'tasks',label:'确认任务数'},{key:'zero_ops_ratio',label:'零操作比例',ratio:true},{key:'avg_ops',label:'平均操作次数'},{key:'median_seconds',label:'停留时间中位数（秒）'},{key:'avg_uncertain',label:'平均不确定项组数'},{key:'kept_ratio',label:'保留比例',ratio:true},{key:'memory_ratio',label:'记忆保留比例',ratio:true}] as const
const stats = computed(()=> fields.map(field=>({label:field.label,value:format(data.value?.[field.key], 'ratio' in field)})))
function format(value?: number, ratio=false) {return value ? new Intl.NumberFormat('zh-CN',{style:ratio?'percent':'decimal',maximumFractionDigits:2}).format(value):'—'}
</script>
<template><div v-loading="loading"><div class="period"><SiSegmented ariaLabel="统计时间范围" v-model="days" :options="[7,30,90,365].map(value=>({value,label:`最近 ${value} 天`}))" /></div><el-alert v-if="error" :title="error" type="error" :closable="false" /><div class="stats"><div v-for="stat in stats" :key="stat.label" class="stat"><span class="stat-label">{{ stat.label }}</span><span class="stat-value">{{ stat.value }}</span></div></div><template v-if="data"><h3>无法判断 · {{ format(data.unable_ratio,true) }}</h3><p>信息已脱敏 {{ data.unable_by_reason.redacted || '—' }} · 文件未写 {{ data.unable_by_reason.absent || '—' }} · 其他 {{ data.unable_by_reason.other || '—' }}</p><p class="hint">因脱敏无法判断最多的要求：{{ data.redacted_top_requirements.map(r=>`${r.req_id} ×${r.count}`).join('、') || '—' }}</p></template></div></template>
<style scoped>.period { display:flex;justify-content:flex-end;margin-bottom:16px; }.stats { display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px; }.stat { display:flex;flex-direction:column;gap:8px; }.stat-value { font:var(--si-font-stat); }h3 { font:var(--si-font-h3);margin:20px 0 8px; }p { margin:8px 0; }</style>
