<script setup lang="ts">
import { onMounted } from 'vue'

import { useDemoStore } from '@/stores/demo'

const demo = useDemoStore()

onMounted(() => {
  void demo.refresh()
})
</script>

<template>
  <div class="demo-banner">
    <el-icon><InfoFilled /></el-icon>
    <span>
      AI 模式：
      <b>{{ demo.isReplay ? '回放（replay）' : '实时（live）' }}</b>
    </span>
    <el-divider direction="vertical" />
    <span>
      业务时间：
      <b>{{ demo.businessTimeText }}</b>
      （Asia/Shanghai）
    </span>
    <el-divider direction="vertical" />
    <span>基准日 {{ demo.baseDateText }}</span>
    <el-divider direction="vertical" />
    <span>时钟偏移 {{ demo.offsetMinutes }} 分钟</span>
    <el-tag v-if="demo.mocked" size="small" type="warning" effect="plain">本地演示数据</el-tag>
    <span class="banner-actions">
      <router-link to="/demo">Demo 控制台</router-link>
      <el-button size="small" text type="primary" :loading="demo.loading" @click="demo.refresh()">
        刷新
      </el-button>
    </span>
  </div>
</template>

<style scoped>
.demo-banner {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding: 6px 16px;
  font-size: 12px;
  background: #fdf6ec;
  border-bottom: 1px solid #f5dab1;
  color: #9a6a1f;
}

.banner-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
