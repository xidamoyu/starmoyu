import { createRouter, createWebHashHistory } from 'vue-router'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', component: () => import('./views/LoginView.vue') },
    {
      path: '/',
      component: () => import('./views/Layout.vue'),
      children: [
        { path: '', component: () => import('./views/ChatView.vue') },
        { path: 'deals', component: () => import('./views/DealList.vue') },
        { path: 'kols', component: () => import('./views/KolManage.vue') },
        { path: 'proposals', component: () => import('./views/ProposalReview.vue') },
        { path: 'import', component: () => import('./views/AdminImport.vue') },
        { path: 'users', component: () => import('./views/UserManage.vue'), meta: { adminOnly: true } },
      ],
    },
  ],
})

// 前端角色守卫：adminOnly 路由非管理员访问时弹回对话页（后端 403 兜底）
router.beforeEach((to) => {
  if (!to.meta.adminOnly) return true
  try {
    const payload = JSON.parse(atob((localStorage.getItem('token') ?? '').split('.')[1] ?? ''))
    if (payload.role === 'admin') return true
  } catch { /* token 缺损按非管理员处理 */ }
  return { path: '/' }
})

export default router
