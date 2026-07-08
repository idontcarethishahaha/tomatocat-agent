<template>
  <div>
    <h1 class="page-title">💭 记忆管理</h1>

    <div class="section-card">
      <div style="display: flex; gap: 12px; margin-bottom: 16px;">
        <el-input
          v-model="searchQuery"
          placeholder="搜索记忆..."
          style="width: 300px;"
          clearable
          @keyup.enter="fetchMemories"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <el-select v-model="memoryType" placeholder="类型筛选" style="width: 200px;" clearable>
          <el-option label="全部" value="" />
          <el-option label="偏好" value="preference" />
          <el-option label="事件" value="event" />
          <el-option label="步骤" value="procedure" />
          <el-option label="画像" value="profile" />
        </el-select>
        <el-button type="primary" @click="fetchMemories">
          <el-icon><Search /></el-icon>
          搜索
        </el-button>
      </div>

      <el-table :data="memories" v-loading="loading" style="width: 100%;">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="memory_type" label="类型" width="120">
          <template #default="{ row }">
            <el-tag :type="getTypeTag(row.memory_type)" size="small">
              {{ row.memory_type || '-' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="summary" label="摘要" min-width="300" />
        <el-table-column prop="weight" label="权重" width="100" />
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button type="danger" size="small" link @click="handleDelete(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div style="margin-top: 16px; text-align: right;">
        <el-pagination
          v-model:current-page="currentPage"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="fetchMemories"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { memoryApi } from '../api'

const memories = ref([])
const loading = ref(false)
const searchQuery = ref('')
const memoryType = ref('')
const currentPage = ref(1)
const pageSize = ref(20)
const total = ref(0)

const fetchMemories = async () => {
  loading.value = true
  try {
    const data = await memoryApi.list({
      q: searchQuery.value,
      memory_type: memoryType.value,
      page: currentPage.value,
      page_size: pageSize.value,
    })
    memories.value = data.items || []
    total.value = data.total || 0
  } catch (e) {
    ElMessage.error('获取记忆列表失败')
  } finally {
    loading.value = false
  }
}

const handleDelete = async (row) => {
  try {
    await ElMessageBox.confirm('确定要删除这条记忆吗？', '提示', {
      type: 'warning',
    })
    await memoryApi.delete(row.id)
    ElMessage.success('删除成功')
    fetchMemories()
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}

const getTypeTag = (type) => {
  const map = {
    preference: 'warning',
    event: 'primary',
    procedure: 'success',
    profile: 'danger',
  }
  return map[type] || 'info'
}

const formatTime = (time) => {
  if (!time) return '-'
  return new Date(time).toLocaleString('zh-CN')
}

onMounted(() => {
  fetchMemories()
})
</script>