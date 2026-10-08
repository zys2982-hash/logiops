import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'
import { errorHandler } from './utils/errorHandler'
import './styles/global.css'

const app = createApp(App)

// 全局注册 Element Plus 图标（侧边栏、面板使用动态组件名）
for (const [name, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(name, component)
}

app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })

app.config.errorHandler = errorHandler

// 等首个路由解析完再挂载。
// 直接 mount 的话首帧 route.meta 还是空的，App.vue 会按"应用布局"渲染出 AppLayout，
// 于是 /login 这类公开页面会先挂载一次 DemoBanner 去请求需要 demo.control 权限的 /demo/state，
// 收到 401 后弹出"登录已过期，请重新登录"并把访问者登出（真机已复现：登录页上无故出现红色报错）。
router.isReady().then(() => app.mount('#app'))
