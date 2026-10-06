<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

// 部署到公网后登录页**不再展示演示账号/口令、也不再预填**（用户口径 2026-10-06）：
// 原来自带 4 个角色的一键填充按钮、并把表单预填成演示账号+统一口令 —— 等于把口令公开。
// 这条约定由 backend/tests/test_cross_layer_contract.py::test_login_page_does_not_leak_demo_credentials 守护
// （它会扫这个文件，所以这里**不能写演示邮箱或口令字面量**）。
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
  <div class="auth-page">
    <el-card class="auth-card" shadow="always">
      <div class="auth-brand">
        <span class="brand-logo">LO</span>
        <div>
          <h2>LogiOps</h2>
          <p class="u-text-muted">物流异常协同平台 · 登录</p>
        </div>
      </div>

      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="邮箱">
          <el-input v-model="form.email" placeholder="you@example.com" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="请输入密码"
            autocomplete="current-password"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button type="primary" style="width: 100%" :loading="submitting" @click="submit">登录</el-button>
      </el-form>

      <el-divider />
      <router-link to="/register">没有账号？去注册</router-link>
    </el-card>
  </div>
</template>

<style scoped>
.auth-page {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  background: linear-gradient(135deg, #1f2d3d 0%, #2f6fed 100%);
}

.auth-card {
  width: 420px;
  border-radius: 10px;
}

.auth-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.auth-brand h2 {
  margin: 0;
}

.brand-logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 8px;
  background: #2f6fed;
  color: #fff;
  font-weight: 700;
}
</style>
