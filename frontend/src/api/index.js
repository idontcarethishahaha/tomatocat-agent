import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 10000,
})

api.interceptors.response.use(
  response => response.data,
  error => {
    console.error('API Error:', error)
    return Promise.reject(error)
  }
)

export const statusApi = {
  getStatus: () => api.get('/status'),
  getHealth: () => api.get('/health'),
}

export const memoryApi = {
  list: (params) => api.get('/memory', { params }),
  delete: (id) => api.delete(`/memory/${id}`),
}

export const skillsApi = {
  list: () => api.get('/skills'),
  detail: (name) => api.get(`/skills/${name}`),
}

export const schedulerApi = {
  list: () => api.get('/scheduler'),
}

export const checkpointsApi = {
  list: () => api.get('/checkpoints'),
}

export const filesApi = {
  list: (path) => api.get('/files/list', { params: { path } }),
  read: (path) => api.get('/files/read', { params: { path } }),
}

export default api