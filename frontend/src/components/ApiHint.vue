<script setup lang="ts">
/** 接口提示：把原来铺在界面上的「契约：POST /xxx …」长注释收进一个悬浮气泡。
 *  演示时鼠标移上去仍能看到"这个面板背后调什么接口"，但平时不占版面。 */
defineProps<{ text: string }>()
</script>

<template>
  <el-tooltip :content="text" placement="top" effect="dark">
    <el-icon class="api-hint"><InfoFilled /></el-icon>
  </el-tooltip>
</template>

<style scoped>
.api-hint {
  color: var(--el-text-color-placeholder, #a8abb2);
  cursor: help;
  vertical-align: middle;
}

.api-hint:hover {
  color: var(--el-color-primary);
}
</style>
