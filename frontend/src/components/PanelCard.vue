<script setup lang="ts">
withDefaults(
  defineProps<{
    title: string
    subtitle?: string
    icon?: string
    bodyClass?: string
  }>(),
  { subtitle: '', icon: '', bodyClass: '' },
)
</script>

<template>
  <el-card shadow="never" class="page-card">
    <template #header>
      <div class="section-title">
        <span class="panel-title">
          <el-icon v-if="icon"><component :is="icon" /></el-icon>
          {{ title }}
          <span v-if="subtitle" class="u-text-muted">{{ subtitle }}</span>
        </span>
        <span><slot name="actions" /></span>
      </div>
    </template>
    <div :class="bodyClass">
      <slot />
    </div>
  </el-card>
</template>

<style scoped>
.panel-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}
</style>
