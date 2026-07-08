<template>
  <div>
    <h1 class="page-title">✨ 技能管理</h1>

    <div class="section-card">
      <el-table :data="skills" v-loading="loading" style="width: 100%;">
        <el-table-column prop="name" label="技能名称" width="200" />
        <el-table-column prop="description" label="描述" min-width="400" />
        <el-table-column prop="source" label="来源" width="120">
          <template #default="{ row }">
            <el-tag :type="row.source === 'builtin' ? 'info' : 'success'" size="small">
              {{ row.source === 'builtin' ? '内置' : '自定义' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="always" label="常驻" width="80">
          <template #default="{ row }">
            <el-tag :type="row.always ? 'success' : 'info'" size="small">
              {{ row.always ? '是' : '否' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button type="primary" size="small" link @click="showDetail(row)">
              查看
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="detailVisible" :title="currentSkill?.name" width="800px">
      <div v-if="currentSkill" style="white-space: pre-wrap; font-family: monospace; background: #f5f7fa; padding: 16px; border-radius: 8px; max-height: 500px; overflow-y: auto;">
        {{ skillContent }}
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { skillsApi } from '../api'

const skills = ref([])
const loading = ref(false)
const detailVisible = ref(false)
const currentSkill = ref(null)
const skillContent = ref('')

const fetchSkills = async () => {
  loading.value = true
  try {
    const data = await skillsApi.list()
    skills.value = data.skills || []
  } catch (e) {
    ElMessage.error('获取技能列表失败')
  } finally {
    loading.value = false
  }
}

const showDetail = async (skill) => {
  currentSkill.value = skill
  detailVisible.value = true
  try {
    const data = await skillsApi.detail(skill.name)
    skillContent.value = data.content || ''
  } catch (e) {
    ElMessage.error('获取技能详情失败')
  }
}

onMounted(() => {
  fetchSkills()
})
</script>