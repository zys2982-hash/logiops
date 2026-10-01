<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="page">
    <el-result icon="warning" title="404" sub-title="页面不存在，或该路由尚未实现">
      <template #extra>
        <el-button type="primary" @click="router.push('/dashboard')">返回总览</el-button>
        <el-button @click="router.push('/exceptions')">去异常中心</el-button>
      </template>
    </el-result>
    <div class="u-text-muted" style="text-align: center">
      已实现路由：/login /register /dashboard /orders /orders/:id /exceptions /exceptions/:id /customers
      /carriers /vehicles /drivers /sla-rules /knowledge /audit /members /demo
    </div>
  </div>
</template>
