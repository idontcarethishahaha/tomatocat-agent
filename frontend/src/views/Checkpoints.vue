<template>
  <div>
    <h1 class="page-title">📌 检查点</h1>

    <div class="section-card">
      <el-table :data="checkpoints" v-loading="loading" style="width: 100%;">
        <el-table-column prop="checkpoint_id" label="ID" width="140" />
        <el-table-column prop="task_type" label="任务类型" width="180">
          <template #default="{ row }">
            <el-tag :type="getTypeTag(row.task_type)" size="small">
              {{ row.task_type }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="current_goal" label="当前目标" min-width="300" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'completed' ? 'success' : row.status === 'active' ? 'primary' : 'warning'" size="small">
              {{ row.status === 'completed' ? '已完成' : row.status === 'active' ? '进行中' : row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="trigger" label="触发" width="120" />
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button type="primary" size="small" link @click="showDetail(row)">
              详情
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && checkpoints.length === 0" description="暂无检查点" />
    </div>

    <el-dialog v-model="detailVisible" title="检查点详情" width="600px">
      <el-descriptions v-if="currentCheckpoint" :column="1" border>
        <el-descriptions-item label="ID">
          {{ currentCheckpoint.checkpoint_id }}
        </el-descriptions-item>
        <el-descriptions-item label="任务类型">
          {{ currentCheckpoint.task_type }}
        </el-descriptions-item>
        <el-descriptions-item label="当前目标">
          {{ currentCheckpoint.current_goal }}
        </el-descriptions-item>
        <el-descriptions-item label="已完成步骤">
          <div v-for="(step, idx) in currentCheckpoint.completed" :key="idx">
            ✅ {{ step }}
          </div>
          <span v-if="!currentCheckpoint.completed?.length">-</span>
        </el-descriptions-item>
        <el-descriptions-item label="下一步">
          {{ currentCheckpoint.next_step || '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="阻塞原因" v-if="currentCheckpoint.blocker">
          <el-tag type="danger">{{ currentCheckpoint.blocker }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="创建时间">
          {{ formatTime(currentCheckpoint.created_at) }}
        </el-descriptions-item>
        <el-descriptions-item label="元数据">
          <pre style="margin: 0; font-size: 12px;">{{ JSON.stringify(currentCheckpoint.metadata, null, 2) }}</pre>
        </el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { checkpointsApi } from '../api'

const checkpoints = ref([])
const loading = ref(false)
const detailVisible = ref(false)
const currentCheckpoint = ref(null)
let timer = null

const fetchCheckpoints = async () => {
  loading.value = true
  try {
    const data = await checkpointsApi.list()
    checkpoints.value = data.checkpoints || []
  } catch (e) {
    ElMessage.error('获取检查点失败')
  } finally {
    loading.value = false
  }
}

const showDetail = (checkpoint) => {
  currentCheckpoint.value = checkpoint
  detailVisible.value = true
}

const getTypeTag = (type) => {
  const map = {
    scheduler: 'primary',
    memory_consolidation: 'success',
  }
  return map[type] || 'info'
}

const formatTime = (time) => {
  if (!time) return '-'
  return new Date(time).toLocaleString('zh-CN')
}

onMounted(() => {
  fetchCheckpoints()
  timer = setInterval(fetchCheckpoints, 5000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>