<script setup lang="ts">
import type { FormInstance, FormRules } from 'element-plus'
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const formRef = ref<FormInstance>()
const form = reactive({ username: '', password: '' })
const submitting = ref(false)
const errorText = ref('')

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

// 只接受站内路径，避免被构造的 redirect 参数跳到别的网站
function safeRedirect(): string {
  const r = route.query.redirect
  return typeof r === 'string' && r.startsWith('/') && !r.startsWith('//') ? r : '/'
}

async function submit() {
  errorText.value = ''
  if (!formRef.value || !(await formRef.value.validate().catch(() => false))) return
  submitting.value = true
  try {
    await auth.login(form.username.trim(), form.password)
    await router.replace(safeRedirect())
  } catch (e) {
    // 账号锁定、停用、用户名或密码错误的说明都来自后端
    errorText.value = e instanceof ApiError ? e.message : '登录失败，请稍后重试'
  } finally {
    form.password = ''
    submitting.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <el-card class="card" shadow="hover">
      <h1 class="title">企业商务文件AI智能审查助手</h1>
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" autocomplete="username" autofocus />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="form.password" type="password" show-password autocomplete="current-password" />
        </el-form-item>
        <el-alert v-if="errorText" type="error" :closable="false" show-icon :title="errorText" class="error" />
        <el-button type="primary" native-type="submit" :loading="submitting" class="submit">登录</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100%;
}

.card {
  width: 380px;
}

.title {
  margin: 0 0 24px;
  font-size: 20px;
  text-align: center;
}

.error {
  margin-bottom: 16px;
}

.submit {
  width: 100%;
}
</style>
