<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import PanelCard from './../components/PanelCard.vue'
import { systemApi } from '@/api'
import { Perm } from '@/types'
import type { KnowledgeChunk, KnowledgeDoc } from '@/types'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/datetime'
import { formatNumber } from '@/utils/format'

const auth = useAuthStore()
const canManage = computed(() => auth.can(Perm.KNOWLEDGE_MANAGE))

const docs = ref<KnowledgeDoc[]>([])
const chunks = ref<KnowledgeChunk[]>([])
const activeDocId = ref<number | null>(null)
const loading = ref(false)
const reindexing = ref(false)
const keyword = ref('')

const activeDoc = computed(() => docs.value.find((doc) => doc.id === activeDocId.value) ?? null)

const filteredChunks = computed(() => {
  if (!keyword.value.trim()) return chunks.value
  const needle = keyword.value.trim().toLowerCase()
  return chunks.value.filter((chunk) => chunk.content.toLowerCase().includes(needle))
})

async function loadDocs(): Promise<void> {
  loading.value = true
  try {
    const page = await systemApi.listKnowledgeDocs({ page: 1, page_size: 100 })
    docs.value = page.items
    if (docs.value.length > 0) await selectDoc(docs.value[0].id)
  } finally {
    loading.value = false
  }
}

async function selectDoc(id: number): Promise<void> {
  activeDocId.value = id
  try {
    const detail = await systemApi.getKnowledgeDoc(id)
    chunks.value = detail.chunks ?? []
  } catch {
    chunks.value = []
  }
}

async function reindex(): Promise<void> {
  reindexing.value = true
  try {
    const result = await systemApi.reindexKnowledge()
    ElMessage.success(`索引重建完成：${result.docs} 篇文档 / ${result.chunks} 个分片`)
    if (result.warning) {
      // 后端契约：索引不可用时仍返回 200，warning 有值
      ElMessage.warning(`索引告警：${result.warning}`)
    }
    await loadDocs()
  } finally {
    reindexing.value = false
  }
}

onMounted(loadDocs)
</script>

<template>
  <div class="page">
    <PanelCard
      title="知识库"
      :subtitle="`${docs.length} 篇文档 · ${formatNumber(chunks.length)} 个分片（MySQL FULLTEXT ngram，不引向量库）`"
      icon="Notebook"
    >
      <template #actions>
        <el-input v-model="keyword" size="small" placeholder="在分片中搜索" clearable style="width: 200px" />
        <el-button v-if="canManage" size="small" type="primary" :loading="reindexing" @click="reindex">
          重建索引
        </el-button>
        <el-tag v-else size="small" effect="plain">只读（knowledge.manage 才可重建）</el-tag>
      </template>

      <el-row :gutter="12">
        <el-col :md="8">
          <el-table :data="docs" v-loading="loading" size="small" border highlight-current-row
            @current-change="(row: KnowledgeDoc | null) => row && selectDoc(row.id)">
            <el-table-column prop="title" label="文档" min-width="150" />
            <el-table-column prop="category" label="分类" width="110" />
            <el-table-column label="分片" width="70" align="center">
              <template #default="{ row }">{{ row.chunk_count ?? '—' }}</template>
            </el-table-column>
          </el-table>
          <div class="u-text-muted u-mt-8">
            语料位置：backend/app/knowledge/*.md（5 篇）· 按 ## 小节切分，chunk 保留 section_path
          </div>
        </el-col>

        <el-col :md="16">
          <el-card v-if="activeDoc" shadow="never" class="page-card u-mb-12">
            <div class="doc-head">
              <b>{{ activeDoc.title }}</b>
              <el-tag size="small" effect="plain">{{ activeDoc.doc_code ?? '—' }}</el-tag>
              <el-tag size="small" effect="plain">v{{ activeDoc.version ?? '—' }}</el-tag>
              <span class="u-text-muted">{{ activeDoc.source_path ?? '—' }}</span>
            </div>
            <div class="u-text-muted">最近更新 {{ formatDateTime(activeDoc.updated_at) }} · checksum {{ (activeDoc.checksum ?? '').slice(0, 12) || '—' }}</div>
          </el-card>

          <el-empty v-if="filteredChunks.length === 0" description="没有匹配的分片" :image-size="60" />
          <div v-for="chunk in filteredChunks" :key="chunk.id" class="chunk-block">
            <div class="chunk-head">
              <el-tag size="small" type="info" effect="plain">#{{ chunk.chunk_no }}</el-tag>
              <b>{{ chunk.section_path ?? '（无小节）' }}</b>
              <span class="u-text-muted u-mono">chunk_id={{ chunk.id }} · ~{{ chunk.token_estimate ?? '?' }} tok</span>
              <el-tag v-if="chunk.score !== null && chunk.score !== undefined" size="small" type="success" effect="plain">
                score {{ chunk.score }}
              </el-tag>
            </div>
            <div class="chunk-content">{{ chunk.content }}</div>
          </div>
          <div class="u-text-muted u-mt-8">
            契约：GET /knowledge/docs · GET /knowledge/docs/{id} · POST /knowledge/reindex（ADMIN+）；
            T2 的 evidence_refs 必须引用真实 chunk_id（§11.6）。
          </div>
        </el-col>
      </el-row>
    </PanelCard>
  </div>
</template>

<style scoped>
.doc-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.chunk-block {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 8px;
  margin-bottom: 8px;
}

.chunk-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}

.chunk-content {
  font-size: 13px;
  line-height: 1.7;
  color: #303133;
}
</style>
