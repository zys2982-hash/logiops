<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import AuthShell from '@/components/AuthShell.vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

const form = reactive({ email: '', password: '', confirm: '', name: '' })
const submitting = ref(false)

async function submit(): Promise<void> {
  if (!form.email || !form.password || !form.name) {
    ElMessage.warning('请填写完整信息')
    return
  }
  if (form.password !== form.confirm) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }
  if (form.password.length < 8) {
    ElMessage.warning('密码至少 8 位')
    return
  }
  submitting.value = true
  try {
    await auth.register({ email: form.email, password: form.password, name: form.name })
    ElMessage.success('注册成功，已自动登录')
    await router.push('/dashboard')
  } catch {
    // 拦截器已提示
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <!-- 与登录页共用同一个视觉外壳（AuthShell） -->
  <AuthShell title="创建账号" subtitle="注册后会自动为你创建一个工作区">
    <el-form label-position="top" @submit.prevent="submit">
      <el-form-item label="姓名">
        <el-input v-model="form.name" size="large" placeholder="请输入姓名" autocomplete="name" />
      </el-form-item>

      <el-form-item label="邮箱">
        <el-input
          v-model="form.email"
          size="large"
          placeholder="you@example.com"
          autocomplete="username"
        />
      </el-form-item>

      <el-form-item label="密码（至少 8 位）">
        <el-input
          v-model="form.password"
          size="large"
          type="password"
          show-password
          placeholder="请设置密码"
          autocomplete="new-password"
        />
      </el-form-item>

      <el-form-item label="确认密码">
        <el-input
          v-model="form.confirm"
          size="large"
          type="password"
          show-password
          placeholder="请再次输入密码"
          autocomplete="new-password"
          @keyup.enter="submit"
        />
      </el-form-item>

      <el-button class="submit-btn" type="primary" size="large" :loading="submitting" @click="submit">
        注册并登录
      </el-button>
    </el-form>

    <template #footer>
      <span>已有账号？</span>
      <router-link to="/login">去登录</router-link>
    </template>
  </AuthShell>
</template>
