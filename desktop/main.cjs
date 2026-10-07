/**
 * LogiOps 桌面客户端 —— Electron 主进程
 *
 * 架构（面试可讲）：
 *   桌面端只做两件事：① 当"壳"（原生窗口、图标、安装包）；② 当"本地网关"。
 *   数据仍在云端后端（FastAPI + MySQL），所以**多人多设备看到的是同一份数据**。
 *
 *   BrowserWindow ──► http://127.0.0.1:<随机端口>
 *                         ├─ 静态文件：随包携带的前端 dist（SPA，带 history 回退）
 *                         └─ /api/*、/docs、/openapi.json ──反向代理──► config.serverUrl
 *
 *   为什么用"本地小服务 + 反向代理"而不是直接加载静态文件：
 *   - 前端仍然只请求相对路径 /api/v1，**前端代码一行都不用改**；
 *   - 没有跨域（浏览器看到的是同源：127.0.0.1 的页面请求 127.0.0.1 的 /api）；
 *   - 服务器地址是运行时可配置的（菜单里改，写进 userData/config.json），换 IP 不用重新打包。
 */
const { app, BrowserWindow, Menu, dialog, shell, ipcMain } = require('electron')
const http = require('http')
const https = require('https')
const fs = require('fs')
const os = require('os')
const path = require('path')

/**
 * 启动打点：默认写系统临时目录 logiops-boot.log，可用 LOGIOPS_BOOT_LOG 指定路径。
 * 用途：Electron 应用若在"还没跑到 whenReady"就退出（单实例锁没拿到、渲染初始化失败等），
 * 界面上什么都看不到、stdout 也可能是空的 —— 这里留下最早期、最可靠的痕迹。
 */
const BOOT_LOG = process.env.LOGIOPS_BOOT_LOG || path.join(os.tmpdir(), 'logiops-boot.log')
function bootLog(message) {
  try {
    fs.appendFileSync(BOOT_LOG, `[${new Date().toISOString()}] ${message}\n`, 'utf8')
  } catch {
    /* 打点失败不影响运行 */
  }
}
bootLog(
  `--- boot --- electron=${process.versions.electron} node=${process.versions.node} ` +
    `runAsNode=${process.env.ELECTRON_RUN_AS_NODE ?? '(unset)'} argv=${process.argv.slice(1).join(' ') || '(none)'}`
)

const DEFAULT_SERVER = 'http://101.200.139.115'
const CONFIG_FILE = () => path.join(app.getPath('userData'), 'config.json')
const DIST_DIR = path.join(__dirname, 'web')

let mainWindow = null
let localPort = null

// ---------------------------------------------------------------------------
// 配置：服务器地址（写进 %APPDATA%/LogiOps/config.json）
// ---------------------------------------------------------------------------
function readConfig() {
  try {
    const raw = fs.readFileSync(CONFIG_FILE(), 'utf8')
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed.serverUrl === 'string' && parsed.serverUrl) return parsed
  } catch {
    /* 首次运行或文件损坏 → 用默认值重建 */
  }
  return { serverUrl: DEFAULT_SERVER }
}

function writeConfig(config) {
  try {
    fs.mkdirSync(path.dirname(CONFIG_FILE()), { recursive: true })
    fs.writeFileSync(CONFIG_FILE(), JSON.stringify(config, null, 2), 'utf8')
    return true
  } catch (error) {
    dialog.showErrorBox('保存配置失败', String(error))
    return false
  }
}

/**
 * 启动日志：写到 %APPDATA%/LogiOps/startup.log。
 * 桌面软件出问题时，用户能直接把这段日志发出来 —— 否则"没窗口、没输出"完全没法排查
 * （2026-10-07 的真实教训：一个启动失败的实例变成了无窗口僵尸进程，锁着文件导致装不上）。
 */
function log(message) {
  try {
    const file = path.join(path.dirname(CONFIG_FILE()), 'startup.log')
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.appendFileSync(file, `[${new Date().toISOString()}] ${message}\n`, 'utf8')
  } catch {
    /* 日志失败不影响使用 */
  }
}

/** 启动环节出错时：必须弹错 + 退出，绝不能留下"没有窗口却还活着"的进程 */
function failFast(title, error) {
  const detail = error && error.stack ? error.stack : String(error)
  log(`FATAL ${title}: ${detail}`)
  try {
    dialog.showErrorBox(title, `${detail}\n\n软件将退出。日志：${path.join(path.dirname(CONFIG_FILE()), 'startup.log')}`)
  } catch {
    /* 连弹窗都失败时也要退出 */
  }
  app.exit(1)
}

process.on('uncaughtException', (error) => failFast('LogiOps 运行异常', error))
process.on('unhandledRejection', (reason) => failFast('LogiOps 运行异常（未处理的 Promise 拒绝）', reason))

let config = { serverUrl: DEFAULT_SERVER }

// ---------------------------------------------------------------------------
// 本地服务：静态文件 + /api 反向代理
// ---------------------------------------------------------------------------
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.mjs': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.gif': 'image/gif',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.map': 'application/json; charset=utf-8'
}

/** 需要转发到后端云服务器的路径前缀（与前端 nginx 配置保持一致） */
function isApiPath(urlPath) {
  return urlPath.startsWith('/api/') || urlPath === '/docs' || urlPath.startsWith('/docs/') || urlPath === '/openapi.json'
}

function proxyToServer(req, res) {
  let target
  try {
    target = new URL(req.url, config.serverUrl)
  } catch {
    res.writeHead(500, { 'content-type': 'text/plain; charset=utf-8' })
    res.end('服务器地址配置无效：' + config.serverUrl)
    return
  }
  const transport = target.protocol === 'https:' ? https : http
  const headers = { ...req.headers, host: target.host }
  const upstream = transport.request(
    {
      protocol: target.protocol,
      hostname: target.hostname,
      port: target.port || (target.protocol === 'https:' ? 443 : 80),
      path: target.pathname + target.search,
      method: req.method,
      headers
    },
    (upstreamRes) => {
      res.writeHead(upstreamRes.statusCode || 502, upstreamRes.headers)
      upstreamRes.pipe(res)
    }
  )
  upstream.on('error', (error) => {
    // 后端挂了/网络不通：返回结构化错误，前端会显示"AI 暂不可用/网络错误"这类提示
    res.writeHead(502, { 'content-type': 'application/json; charset=utf-8' })
    res.end(
      JSON.stringify({
        error: {
          code: 'UPSTREAM_UNAVAILABLE',
          message: `连不上后端服务器 ${config.serverUrl}（${error.code || error.message}）`,
          details: { server: config.serverUrl }
        }
      })
    )
  })
  req.pipe(upstream)
}

function serveStatic(req, res) {
  const urlPath = decodeURIComponent((req.url || '/').split('?')[0])
  const safePath = path
    .normalize(urlPath)
    .replace(/^([/\\])+/, '')
    .replace(/^(\.\.[/\\])+/, '')
  let filePath = path.join(DIST_DIR, safePath)
  if (!filePath.startsWith(DIST_DIR)) {
    res.writeHead(403)
    res.end()
    return
  }
  if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
    if (fs.existsSync(path.join(filePath, 'index.html'))) {
      filePath = path.join(filePath, 'index.html')
    } else {
      // SPA（Vue Router history 模式）：未知路径回退到 index.html
      filePath = path.join(DIST_DIR, 'index.html')
    }
  }
  fs.readFile(filePath, (error, data) => {
    if (error) {
      res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' })
      res.end('404')
      return
    }
    res.writeHead(200, { 'content-type': MIME[path.extname(filePath).toLowerCase()] || 'application/octet-stream' })
    res.end(data)
  })
}

function startLocalServer() {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      if (isApiPath((req.url || '').split('?')[0])) proxyToServer(req, res)
      else serveStatic(req, res)
    })
    server.on('error', reject)
    // 端口交给系统随机分配（避免和本机已占用的端口冲突），只监听 127.0.0.1
    server.listen(0, '127.0.0.1', () => {
      localPort = server.address().port
      resolve(localPort)
    })
  })
}

// ---------------------------------------------------------------------------
// 窗口
// ---------------------------------------------------------------------------
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    title: 'LogiOps 物流异常协同平台',
    backgroundColor: '#1f2d3d',
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false
    }
  })
  mainWindow.removeMenu?.()
  mainWindow.once('ready-to-show', () => mainWindow.show())
  mainWindow.loadURL(`http://127.0.0.1:${localPort}/`)

  // 载入后探一次后端健康状态：连不上就用系统弹窗提示（比页面上的报错更清楚）
  mainWindow.webContents.once('did-finish-load', async () => {
    try {
      const ok = await probeHealth()
      if (!ok) {
        const { response } = await dialog.showMessageBox(mainWindow, {
          type: 'warning',
          title: '连不上后端服务器',
          message: `当前服务器地址：${config.serverUrl}`,
          detail: '软件界面可以打开，但数据读不到。请检查网络，或在菜单「服务器 → 设置服务器地址…」里改成正确的地址。',
          buttons: ['重新检测', '打开设置', '继续（离线看界面）'],
          defaultId: 0,
          cancelId: 2
        })
        if (response === 0) mainWindow.reload()
        if (response === 1) openSettings()
      }
    } catch {
      /* 探测失败不阻塞使用 */
    }
  })

  // 外链用系统浏览器打开
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })
}

function probeHealth() {
  return new Promise((resolve) => {
    let target
    try {
      target = new URL('/api/v1/healthz', config.serverUrl)
    } catch {
      return resolve(false)
    }
    const transport = target.protocol === 'https:' ? https : http
    const req = transport.request(
      { hostname: target.hostname, port: target.port || 80, path: target.pathname, method: 'GET', timeout: 6000 },
      (res) => {
        res.resume()
        resolve((res.statusCode || 0) < 500)
      }
    )
    req.on('timeout', () => {
      req.destroy()
      resolve(false)
    })
    req.on('error', () => resolve(false))
    req.end()
  })
}

// ---------------------------------------------------------------------------
// 设置窗口（改服务器地址）
// ---------------------------------------------------------------------------
let settingsWindow = null
function openSettings() {
  if (settingsWindow) {
    settingsWindow.focus()
    return
  }
  settingsWindow = new BrowserWindow({
    width: 520,
    height: 300,
    resizable: false,
    minimizable: false,
    maximizable: false,
    title: '设置服务器地址',
    parent: mainWindow,
    modal: false,
    webPreferences: { preload: path.join(__dirname, 'settings-preload.cjs'), contextIsolation: true }
  })
  settingsWindow.removeMenu?.()
  settingsWindow.loadFile(path.join(__dirname, 'settings.html'))
  settingsWindow.on('closed', () => {
    settingsWindow = null
  })
}

ipcMain.handle('logiops:get-server', () => config.serverUrl)
ipcMain.handle('logiops:save-server', (_event, rawUrl) => {
  const url = String(rawUrl || '').trim().replace(/\/+$/, '')
  if (!/^https?:\/\/.+/i.test(url)) return { ok: false, message: '地址要以 http:// 或 https:// 开头' }
  config = { ...config, serverUrl: url }
  if (!writeConfig(config)) return { ok: false, message: '写入配置文件失败' }
  if (settingsWindow) settingsWindow.close()
  if (mainWindow) mainWindow.reload()
  return { ok: true }
})
ipcMain.handle('logiops:open-config-file', () => shell.openPath(CONFIG_FILE()))
ipcMain.handle('logiops:close-settings', () => {
  if (settingsWindow) settingsWindow.close()
})
ipcMain.handle('logiops:info', () => ({
  version: app.getVersion(),
  serverUrl: config.serverUrl,
  configPath: CONFIG_FILE(),
  electron: process.versions.electron,
  chrome: process.versions.chrome
}))

function buildMenu() {
  const template = [
    {
      label: '服务器',
      submenu: [
        { label: '设置服务器地址…', accelerator: 'CmdOrCtrl+,', click: openSettings },
        {
          label: '打开配置文件（config.json）',
          click: () => shell.openPath(CONFIG_FILE())
        },
        { type: 'separator' },
        {
          label: '刷新（重新加载界面）',
          accelerator: 'F5',
          click: () => mainWindow && mainWindow.reload()
        },
        {
          label: '检测后端连接',
          click: async () => {
            const ok = await probeHealth()
            dialog.showMessageBox(mainWindow, {
              type: ok ? 'info' : 'warning',
              title: '后端连接检测',
              message: ok ? '后端连接正常 ✓' : '连不上后端 ✗',
              detail: `服务器地址：${config.serverUrl}`
            })
          }
        }
      ]
    },
    {
      label: '视图',
      submenu: [
        { label: '放大', role: 'zoomIn' },
        { label: '缩小', role: 'zoomOut' },
        { label: '复位', role: 'resetZoom' },
        { type: 'separator' },
        { label: '全屏', role: 'togglefullscreen' },
        { label: '开发者工具', role: 'toggleDevTools' }
      ]
    },
    {
      label: '帮助',
      submenu: [
        {
          label: '关于 LogiOps',
          click: () => {
            dialog.showMessageBox(mainWindow, {
              type: 'info',
              title: '关于',
              message: `LogiOps 桌面客户端 v${app.getVersion()}`,
              detail: [
                `后端服务器：${config.serverUrl}`,
                `配置文件：${CONFIG_FILE()}`,
                `Electron ${process.versions.electron} / Chromium ${process.versions.chrome}`,
                '',
                '数据保存在云端后端，多人多设备共享同一份数据。'
              ].join('\n')
            })
          }
        }
      ]
    }
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

// ---------------------------------------------------------------------------
// 启动
// ---------------------------------------------------------------------------
bootLog(`userData=${app.getPath('userData')} appName=${app.getName()} packaged=${app.isPackaged}`)
const gotLock = app.requestSingleInstanceLock()
bootLog(`singleInstanceLock=${gotLock}`)
if (!gotLock) {
  // 已有实例在跑：正常行为是"把已有窗口拉到前面"。但如果那个实例是坏的（没有窗口），
  // 用户会看到"双击没反应"。这里把原因写进打点日志，方便排查。
  bootLog('未拿到单实例锁（已有实例在运行）→ 退出')
  app.quit()
} else {
  app.on('second-instance', () => {
    bootLog('收到 second-instance：把已有窗口拉到前面')
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
    }
  })

  app.whenReady().then(async () => {
    bootLog('whenReady 已触发')
    try {
      config = readConfig()
      writeConfig(config)
      log(`启动：serverUrl=${config.serverUrl} version=${app.getVersion()}`)
      const port = await startLocalServer()
      log(`本地服务已启动：http://127.0.0.1:${port}`)
      bootLog(`本地服务端口=${port}`)
      buildMenu()
      createWindow()
      log('窗口已创建')
      bootLog('窗口已创建')
    } catch (error) {
      // 关键：启动失败必须弹错并退出。否则主进程会一直活着（没有窗口），
      // 既占着单实例锁、又锁住 exe 文件 → 下一次安装会卡在"无法关闭"。
      failFast('LogiOps 启动失败', error)
    }
  })

  app.on('window-all-closed', () => {
    bootLog('所有窗口已关闭 → 退出')
    app.quit()
  })
}
