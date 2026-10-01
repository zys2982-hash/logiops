// TypeScript 只认识 vue 文件中的模板；全局注册的图标组件需要声明
export {}

declare module 'vue' {
  export interface GlobalComponents {
    [key: string]: unknown
  }
}
