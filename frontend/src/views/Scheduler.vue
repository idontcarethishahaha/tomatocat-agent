<template>
  <div>
    <h1 class="page-title">⏰ 定时任务</h1>

    <div class="section-card">
      <el-table :data="jobs" v-loading="loading" style="width: 100%;">
        <el-table-column prop="id" label="ID" width="120" />
        <el-table-column prop="name" label="任务名称" width="200" />
        <el-table-column prop="trigger" label="触发方式" width="120">
          <template #default="{ row }">
            <el-tag type="primary" size="small">{{ row.trigger }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="mode" label="模式" width="100">
          <template #default="{ row }">
            <el-tag :type="row.mode === 'instant' ? 'success' : 'warning'" size="small">
              {{ row.mode }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="fire_at" label="下次执行时间" width="200">
          <template #default="{ row }">
            {{ formatTime(row.fire_at) }}
          </template>
        </el-table-column>
        <el-table-column prop="run_count" label="执行次数" width="100" />
      </el-table>

      <el-empty v-if="!loading && jobs.length === 0" description="暂无定时任务" />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { schedulerApi } from '../api'

const jobs = ref([])
const loading = ref(false)
let timer = null

const fetchJobs = async () => {
  loading.value = true
  try {
    const data = await schedulerApi.list()
    jobs.value = data.jobs || []
  } catch (e) {
    ElMessage.error('获取定时任务失败')
  } finally {
    loading.value = false
  }
}

const formatTime = (time) => {
  if (!time) return '-'
  return new Date(time).toLocaleString('zh-CN')
}

onMounted(() => {
  fetchJobs()
  timer = setInterval(fetchJobs, 10000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>