<template>
  <div>
    <h1 class="page-title">📁 文件浏览器</h1>

    <div class="section-card">
      <div style="margin-bottom: 16px;">
        <el-breadcrumb separator="/">
          <el-breadcrumb-item @click="navigateTo('')">
            <el-icon><House /></el-icon>
            工作区
          </el-breadcrumb-item>
          <el-breadcrumb-item
            v-for="(item, idx) in breadcrumbs"
            :key="idx"
            @click="navigateTo(item.path)"
          >
            {{ item.name }}
          </el-breadcrumb-item>
        </el-breadcrumb>
      </div>

      <el-table :data="files" v-loading="loading" style="width: 100%;" @row-click="handleRowClick">
        <el-table-column prop="name" label="名称">
          <template #default="{ row }">
            <span style="display: flex; align-items: center; gap: 8px;">
              <el-icon v-if="row.is_dir" style="color: #409eff;"><Folder /></el-icon>
              <el-icon v-else style="color: #909399;"><Document /></el-icon>
              {{ row.name }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="size" label="大小" width="120">
          <template #default="{ row }">
            {{ row.is_dir ? '-' : formatSize(row.size) }}
          </template>
        </el-table-column>
        <el-table-column prop="modified" label="修改时间" width="200">
          <template #default="{ row }">
            {{ formatTime(row.modified) }}
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="fileVisible" :title="currentFile" width="800px">
      <pre v-if="fileContent" style="background: #f5f7fa; padding: 16px; border-radius: 8px; max-height: 500px; overflow: auto; font-family: monospace; font-size: 13px;">{{ fileContent }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { filesApi } from '../api'

const files = ref([])
const loading = ref(false)
const currentPath = ref('')
const fileVisible = ref(false)
const currentFile = ref('')
const fileContent = ref('')

const breadcrumbs = computed(() => {
  if (!currentPath.value) return []
  const parts = currentPath.value.split('/')
  let path = ''
  return parts.filter(Boolean).map(part => {
    path += (path ? '/' : '') + part
    return { name: part, path }
  })
})

const fetchFiles = async () => {
  loading.value = true
  try {
    const data = await filesApi.list(currentPath.value)
    files.value = data.items || []
  } catch (e) {
    ElMessage.error('获取文件列表失败')
  } finally {
    loading.value = false
  }
}

const navigateTo = (path) => {
  currentPath.value = path
  fetchFiles()
}

const handleRowClick = async (row) => {
  const fullPath = currentPath.value ? `${currentPath.value}/${row.name}` : row.name
  if (row.is_dir) {
    navigateTo(fullPath)
  } else {
    currentFile.value = row.name
    fileVisible.value = true
    try {
      const data = await filesApi.read(fullPath)
      fileContent.value = data.content || ''
    } catch (e) {
      ElMessage.error('读取文件失败')
    }
  }
}

const formatSize = (bytes) => {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const formatTime = (time) => {
  if (!time) return '-'
  return new Date(time).toLocaleString('zh-CN')
}

onMounted(() => {
  fetchFiles()
})
</script>

<style scoped>
.el-table__row {
  cursor: pointer;
}
</style>