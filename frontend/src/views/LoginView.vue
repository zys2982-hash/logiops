<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const form = reactive({ email: 'admin@logiops.dev', password: 'Demo@12345' })
const submitting = ref(false)

// 演示账号域名必须与后端 seed（backend/app/seed/catalog.py）完全一致，否则点按钮会 401。
// 这条跨层约定由 backend/tests/test_cross_layer_contract.py 守护。
const demoAccounts = [
  { email: 'owner@logiops.dev', role: 'OWNER' },
  { email: 'admin@logiops.dev', role: 'ADMIN' },
  { email: 'operator@logiops.dev', role: 'OPERATOR' },
  { email: 'viewer@logiops.dev', role: 'VIEWER' },
]

function pick(email: string): void {
  form.email = email
  form.password = 'Demo@12345'
}

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
          <el-input v-model="form.email" placeholder="admin@logiops.dev" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="Demo@12345"
            autocomplete="current-password"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button type="primary" style="width: 100%" :loading="submitting" @click="submit">登录</el-button>
      </el-form>

      <el-divider>演示账号（密码统一 Demo@12345）</el-divider>
      <div class="demo-accounts">
        <el-button v-for="account in demoAccounts" :key="account.email" size="small" @click="pick(account.email)">
          {{ account.role }}
        </el-button>
      </div>
      <p class="u-text-muted">
        后端未就绪时，接口会回落到本地 fixture，登录后仍可浏览全部页面骨架。
      </p>
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

.demo-accounts {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
