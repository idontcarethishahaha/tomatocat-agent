import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/dashboard' },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('../views/Dashboard.vue'),
    meta: { title: '仪表盘', icon: 'DataLine' },
  },
  {
    path: '/memory',
    name: 'Memory',
    component: () => import('../views/Memory.vue'),
    meta: { title: '记忆管理', icon: 'Coin' },
  },
  {
    path: '/skills',
    name: 'Skills',
    component: () => import('../views/Skills.vue'),
    meta: { title: '技能管理', icon: 'MagicStick' },
  },
  {
    path: '/scheduler',
    name: 'Scheduler',
    component: () => import('../views/Scheduler.vue'),
    meta: { title: '定时任务', icon: 'AlarmClock' },
  },
  {
    path: '/checkpoints',
    name: 'Checkpoints',
    component: () => import('../views/Checkpoints.vue'),
    meta: { title: '检查点', icon: 'RefreshLeft' },
  },
  {
    path: '/files',
    name: 'Files',
    component: () => import('../views/Files.vue'),
    meta: { title: '文件浏览器', icon: 'Folder' },
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

export default router