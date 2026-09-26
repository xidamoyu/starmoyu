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
      ],
    },
  ],
})

export default router
