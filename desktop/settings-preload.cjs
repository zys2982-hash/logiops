/** 设置窗口的 preload：只暴露三个最小能力（contextIsolation 下安全传参） */
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('logiopsSettings', {
  current: () => ipcRenderer.invoke('logiops:get-server'),
  save: (url) => ipcRenderer.invoke('logiops:save-server', url),
  close: () => ipcRenderer.invoke('logiops:close-settings')
})
