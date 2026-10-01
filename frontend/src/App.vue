<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import AppLayout from '@/components/layout/AppLayout.vue'

const route = useRoute()
const layout = computed(() => (route.meta.layout === 'auth' ? 'auth' : 'app'))
</script>

<template>
  <router-view v-if="layout === 'auth'" />
  <AppLayout v-else />
</template>
