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
    <el-tooltip
      placement="bottom"
      raw-content
      content="本次演示的 AI 分析读取<b>预录样本</b>（后端 AI_MODE=replay），不调用真实大模型，结果可复现。<br/>要改成真实调用，请在后端 .env 设 AI_MODE=live 并重启。"
    >
      <span class="banner-source">
        AI 分析来源：<b>{{ demo.isReplay ? '回放样本（replay）' : '真实大模型（live）' }}</b>
      </span>
    </el-tooltip>
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
      <router-link to="/demo">演示工具</router-link>
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

/* 提示这是"来源说明"，不是可点击的模式开关 */
.banner-source {
  cursor: help;
  text-decoration: underline dotted;
  text-underline-offset: 3px;
}
</style>
