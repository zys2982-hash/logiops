<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

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
  <div class="auth-page">
    <el-card class="auth-card" shadow="always">
      <h2>注册 LogiOps 账号</h2>
      <p class="u-text-muted">POST /auth/register → 201 user（注册后可创建/加入工作区）</p>
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="姓名">
          <el-input v-model="form.name" placeholder="张三" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="form.email" placeholder="you@example.com" />
        </el-form-item>
        <el-form-item label="密码（≥8 位）">
          <el-input v-model="form.password" type="password" show-password />
        </el-form-item>
        <el-form-item label="确认密码">
          <el-input v-model="form.confirm" type="password" show-password @keyup.enter="submit" />
        </el-form-item>
        <el-button type="primary" style="width: 100%" :loading="submitting" @click="submit">注册并登录</el-button>
      </el-form>
      <el-divider />
      <router-link to="/login">已有账号？去登录</router-link>
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
</style>
