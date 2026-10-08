<script setup lang="ts">
/**
 * 修改密码弹窗（POST /api/v1/auth/password）。
 *
 * 约定：
 * - 只改"当前登录用户自己"的密码：后端从 token 取目标用户，接口不接受 user_id/email。
 * - 原密码错误时后端返回 **422 VALIDATION_ERROR**（不是 401）—— 401 会被 axios 拦截器
 *   当成"会话失效"并执行 logoutLocal()，打错一次原密码就被踢下线（见 stores/auth.ts）。
 * - 失败提示统一由 axios 拦截器弹，这里不重复弹。
 */
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'

import { changePassword } from '@/api/auth'

const visible = defineModel<boolean>({ default: false })

const formRef = ref<FormInstance>()
const submitting = ref(false)

const form = reactive({
  old_password: '',
  new_password: '',
  confirm_password: '',
})

/** 强度打分 0-4：长度≥8 / 字母+数字 / 含符号 / 长度≥12 */
const strength = computed(() => {
  const value = form.new_password
  if (!value) return 0
  let score = 0
  if (value.length >= 8) score += 1
  if (/[A-Za-z]/.test(value) && /\d/.test(value)) score += 1
  if (/[^A-Za-z0-9]/.test(value)) score += 1
  if (value.length >= 12) score += 1
  return score
})

const strengthMeta = computed(() => {
  const table = [
    { text: '', color: '#dcdfe6' },
    { text: '太弱', color: '#f56c6c' },
    { text: '偏弱', color: '#e6a23c' },
    { text: '中等', color: '#2f6fed' },
    { text: '很强', color: '#21a675' },
  ]
  return table[strength.value]
})

const rules: FormRules = {
  old_password: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 8, max: 64, message: '新密码需 8-64 位', trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) => {
        if (value && !(/[A-Za-z]/.test(value) && /\d/.test(value))) {
          callback(new Error('新密码需同时包含字母和数字'))
          return
        }
        if (value && value === form.old_password) {
          callback(new Error('新密码不能与原密码相同'))
          return
        }
        callback()
      },
      trigger: 'blur',
    },
  ],
  confirm_password: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) => {
        if (value !== form.new_password) {
          callback(new Error('两次输入的新密码不一致'))
          return
        }
        callback()
      },
      trigger: 'blur',
    },
  ],
}

watch(visible, (opened) => {
  if (!opened) return
  form.old_password = ''
  form.new_password = ''
  form.confirm_password = ''
  formRef.value?.clearValidate()
})

async function submit(): Promise<void> {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    const result = await changePassword({
      old_password: form.old_password,
      new_password: form.new_password,
    })
    ElMessage.success(result.message || '密码已修改')
    visible.value = false
  } catch {
    // 错误提示由 axios 拦截器统一处理
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" title="修改密码" width="440px" append-to-body>
    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="改完请用新密码重新登录"
      description="服务端不维护登录凭证黑名单，其他设备上已登录的旧凭证在过期前仍然有效。"
      style="margin-bottom: 16px"
    />

    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
      <el-form-item label="原密码" prop="old_password">
        <el-input
          v-model="form.old_password"
          type="password"
          show-password
          placeholder="请输入当前密码"
          autocomplete="current-password"
        />
      </el-form-item>

      <el-form-item label="新密码" prop="new_password">
        <el-input
          v-model="form.new_password"
          type="password"
          show-password
          placeholder="8-64 位，需含字母和数字"
          autocomplete="new-password"
        />
        <div class="strength">
          <span class="strength-bars">
            <i
              v-for="index in 4"
              :key="index"
              class="strength-bar"
              :style="{ background: index <= strength ? strengthMeta.color : '#ebeef5' }"
            />
          </span>
          <span class="strength-text" :style="{ color: strengthMeta.color }">{{ strengthMeta.text }}</span>
        </div>
      </el-form-item>

      <el-form-item label="确认新密码" prop="confirm_password">
        <el-input
          v-model="form.confirm_password"
          type="password"
          show-password
          placeholder="再次输入新密码"
          autocomplete="new-password"
          @keyup.enter="submit"
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">确认修改</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.strength {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  width: 100%;
}

.strength-bars {
  display: flex;
  gap: 4px;
}

.strength-bar {
  display: block;
  width: 32px;
  height: 4px;
  border-radius: 2px;
  transition: background 0.2s;
}

.strength-text {
  font-size: 12px;
  line-height: 1;
}
</style>
