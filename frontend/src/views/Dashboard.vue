<template>
  <div>
    <h1 class="page-title">📊 仪表盘</h1>

    <div class="card-grid">
      <div class="stat-card">
        <el-icon class="stat-icon"><DataLine /></el-icon>
        <div class="stat-label">运行时长</div>
        <div class="stat-value">{{ status?.uptime || '-' }}</div>
      </div>
      <div class="stat-card">
        <el-icon class="stat-icon"><ChatDotRound /></el-icon>
        <div class="stat-label">消息数</div>
        <div class="stat-value">{{ status?.total_messages || 0 }}</div>
      </div>
      <div class="stat-card">
        <el-icon class="stat-icon"><MagicStick /></el-icon>
        <div class="stat-label">技能数</div>
        <div class="stat-value">{{ status?.skill_count || 0 }}</div>
      </div>
      <div class="stat-card">
        <el-icon class="stat-icon"><AlarmClock /></el-icon>
        <div class="stat-label">定时任务</div>
        <div class="stat-value">{{ status?.scheduler_count || 0 }}</div>
      </div>
    </div>

    <div class="section-card">
      <h2 class="section-title">⚙️ 系统信息</h2>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="工作目录">{{ status?.workspace || '-' }}</el-descriptions-item>
        <el-descriptions-item label="启动时间">{{ formatTime(status?.start_time) }}</el-descriptions-item>
        <el-descriptions-item label="工具调用总数">{{ status?.total_tool_calls || 0 }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag type="success">运行中</el-tag>
        </el-descriptions-item>
      </el-descriptions>
    </div>

    <div class="section-card">
      <h2 class="section-title">🎯 快捷操作</h2>
      <div class="card-grid" style="margin-bottom: 0;">
        <el-card shadow="hover" class="action-card" @click="$router.push('/memory')">
          <div style="text-align: center; padding: 20px;">
            <el-icon size="40" style="color: #ff6b6b;"><Coin /></el-icon>
            <h3 style="margin-top: 12px;">记忆管理</h3>
            <p style="color: #909399; font-size: 14px;">查看和管理番茄猫的记忆</p>
          </div>
        </el-card>
        <el-card shadow="hover" class="action-card" @click="$router.push('/skills')">
          <div style="text-align: center; padding: 20px;">
            <el-icon size="40" style="color: #ffa07a;"><MagicStick /></el-icon>
            <h3 style="margin-top: 12px;">技能管理</h3>
            <p style="color: #909399; font-size: 14px;">查看和管理技能</p>
          </div>
        </el-card>
        <el-card shadow="hover" class="action-card" @click="$router.push('/scheduler')">
          <div style="text-align: center; padding: 20px;">
            <el-icon size="40" style="color: #95ec69;"><AlarmClock /></el-icon>
            <h3 style="margin-top: 12px;">定时任务</h3>
            <p style="color: #909399; font-size: 14px;">管理定时任务</p>
          </div>
        </el-card>
        <el-card shadow="hover" class="action-card" @click="$router.push('/files')">
          <div style="text-align: center; padding: 20px;">
            <el-icon size="40" style="color: #409eff;"><Folder /></el-icon>
            <h3 style="margin-top: 12px;">文件浏览器</h3>
            <p style="color: #909399; font-size: 14px;">浏览工作区文件</p>
          </div>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { statusApi } from '../api'

const status = ref(null)
let timer = null

const fetchStatus = async () => {
  try {
    const data = await statusApi.getStatus()
    status.value = data
  } catch (e) {
    console.error('获取状态失败', e)
  }
}

const formatTime = (time) => {
  if (!time) return '-'
  return new Date(time).toLocaleString('zh-CN')
}

onMounted(() => {
  fetchStatus()
  timer = setInterval(fetchStatus, 5000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.action-card {
  cursor: pointer;
  transition: transform 0.2s;
}
.action-card:hover {
  transform: translateY(-4px);
}
</style>