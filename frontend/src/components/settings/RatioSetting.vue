<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { adminApi } from '@/api/admin'
import { ApiError } from '@/api/http'
const value = ref(false), loading = ref(true), error=ref('')
const description = '识别到财务报表的审核与追问，把本机算好的毛利率、资产负债率等比率发给模型分析；绝对金额仍不发送。比率仍是经营敏感信息，请按需开启。'
onMounted(async()=>{try{value.value=(await adminApi.reviewSettings()).send_ratios}catch(e){error.value=e instanceof ApiError?e.message:'设置加载失败，请刷新页面'}finally{loading.value=false}})
async function change(next: string|number|boolean) {
 if(loading.value)return
 if(next===true) {try {await ElMessageBox.confirm(description,'开启发送本地财务比率',{type:'warning',confirmButtonText:'开启',cancelButtonText:'取消'})}catch{return}}
 loading.value=true;error.value=''
 try{value.value=(await adminApi.saveReviewSettings({send_ratios:next===true})).send_ratios;ElMessage.success('已保存')}catch(e){error.value=e instanceof ApiError?e.message:'设置保存失败，请重试'}finally{loading.value=false}
}
</script>
<template><div><el-alert v-if="error" type="error" :title="error" :closable="false" /><div class="row"><p class="hint">{{ description }}</p><el-switch :model-value="value" :disabled="!!error" :loading="loading" aria-label="发送本地财务比率" @change="change" /></div></div></template>
<style scoped>.row {display:flex;align-items:center;gap:20px;}.hint {flex:1;margin:0;}</style>
