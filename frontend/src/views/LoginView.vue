<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import AuthShell from '@/components/AuthShell.vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

// 部署到公网后登录页**不再展示演示账号/口令、也不再预填**（用户口径 2026-10-06）：
// 原来自带 4 个角色的一键填充按钮、并把表单预填成演示账号+统一口令 —— 等于把口令公开。
// 这条约定由 backend/tests/test_cross_layer_contract.py::test_login_page_does_not_leak_demo_credentials 守护
// （它会扫本文件，所以这里**不能写演示邮箱或口令字面量**）。
const form = reactive({ email: '', password: '' })
const submitting = ref(false)

async function submit(): Promise<void> {
  if (!form.email || !form.password) {
    ElMessage.warning('请输入邮箱与密码')
    return
  }
  submitting.value = true
  try {
    await auth.login({ email: form.email, password: form.password })
    ElMessage.success('登录成功')
    const redirect = route.query.redirect
    await router.push(typeof redirect === 'string' ? redirect : '/dashboard')
  } catch {
    // 错误提示由 axios 拦截器统一处理
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <!-- 视觉外壳（蓝色底 + 基础形状）在 AuthShell 里，与注册页共用同一套 -->
  <AuthShell title="欢迎回来" subtitle="请使用你的工作区账号登录">
    <el-form label-position="top" @submit.prevent="submit">
      <el-form-item label="邮箱">
        <el-input
          v-model="form.email"
          size="large"
          placeholder="you@example.com"
          autocomplete="username"
        />
      </el-form-item>

      <el-form-item label="密码">
        <el-input
          v-model="form.password"
          size="large"
          type="password"
          show-password
          placeholder="请输入密码"
          autocomplete="current-password"
          @keyup.enter="submit"
        />
      </el-form-item>

      <el-button class="submit-btn" type="primary" size="large" :loading="submitting" @click="submit">
        登录
      </el-button>
    </el-form>

    <template #footer>
      <span>还没有账号？</span>
      <router-link to="/register">立即注册</router-link>
    </template>
  </AuthShell>
</template>
