<template>
  <div class="app-container">
    <aside class="app-sidebar">
      <div class="logo">🍅🐱 番茄猫</div>
      <nav>
        <div
          v-for="route in menuRoutes"
          :key="route.path"
          class="menu-item"
          :class="{ active: $route.path === route.path }"
          @click="$router.push(route.path)"
        >
          <el-icon><component :is="route.meta.icon" /></el-icon>
          <span>{{ route.meta.title }}</span>
        </div>
      </nav>
    </aside>
    <main class="app-main">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'

const router = useRouter()
const route = useRoute()

const menuRoutes = computed(() => {
  return router.options.routes
    .filter(r => r.meta && r.meta.title)
    .map(r => ({
      path: r.path,
      meta: r.meta,
    }))
})
</script>