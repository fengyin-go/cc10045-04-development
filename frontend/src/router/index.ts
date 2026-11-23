import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import SharedPackageView from '@/views/SharedPackageView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/shared-packages', name: 'shared-packages', component: SharedPackageView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
