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
// 应用名 / 用户数据目录：**必须在 ready 之前、且在任何 getPath 使用之前设定**
// （实测打包后 app.getName() 取的是 package.json 的 name=logiops-desktop，
//   导致配置写到 %APPDATA%\logiops-desktop，与文档/菜单里显示的路径不一致）
app.setName('LogiOps')
app.setPath('userData', path.join(app.getPath('appData'), 'LogiOps'))

// 企业网络常有 HTTP 代理：确保访问**本机本地服务**（127.0.0.1）时永远不走代理，
// 否则页面加载可能被代理拦掉 → 窗口一片空白（"打开了没显示"）。
app.commandLine.appendSwitch('proxy-bypass-list', '127.0.0.1,localhost')

/**
 * 启动打点：固定写在 %APPDATA%\LogiOps\boot.log（可用 LOGIOPS_BOOT_LOG 覆盖）。
 * 用固定路径而不是系统临时目录 —— 不同启动方式（双击 / 命令行 / 计划任务）的 TEMP
 * 可能不一样，日志散落各处就查不到了（2026-10-07 实测踩过）。
 */
const BOOT_LOG = process.env.LOGIOPS_BOOT_LOG || path.join(app.getPath('userData'), 'boot.log')
function bootLog(message) {
  try {
    fs.mkdirSync(path.dirname(BOOT_LOG), { recursive: true })
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
/**
 * 发布模式（决定"改前端要不要重新打包软件"）：
 * - `remote-first`（默认）：页面/资源优先从服务器取 → **改前端只需更新服务器，所有客户端刷新即最新**；
 *   服务器不可用时按"本地缓存 → 包内副本"逐级回退，界面照样能打开。
 * - `local-only`：只用本地缓存/包内副本（纯离线演示用；前端更新需重新打包）。
 */
const PUBLISH_MODES = ['remote-first', 'local-only']
const DEFAULT_PUBLISH_MODE = 'remote-first'

function readConfig() {
  try {
    const raw = fs.readFileSync(CONFIG_FILE(), 'utf8')
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed.serverUrl === 'string' && parsed.serverUrl) {
      return {
        serverUrl: parsed.serverUrl,
        mode: PUBLISH_MODES.includes(parsed.mode) ? parsed.mode : DEFAULT_PUBLISH_MODE
      }
    }
  } catch {
    /* 首次运行或文件损坏 → 用默认值重建 */
  }
  return { serverUrl: DEFAULT_SERVER, mode: DEFAULT_PUBLISH_MODE }
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

let config = { serverUrl: DEFAULT_SERVER, mode: DEFAULT_PUBLISH_MODE }

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

// ---------------------------------------------------------------------------
// 页面资源：远端优先 → 本地缓存 → 包内副本（三级回退）
//
// 为什么要这样：
// - 只放包内副本 → 改前端必须重新打包分发（"版本化成本"）；
// - 只放远端（套壳浏览器）→ 服务器/网络一抖就白屏，演示时最致命；
// - 远端优先 + 缓存回退 → 两者兼得：平时改前端只更新服务器；断网时用上次缓存的最新版；
//   从没联过网时还能用安装包里那份兜底。
// ---------------------------------------------------------------------------
const REMOTE_TIMEOUT_MS = 2500 // 单个资源的远端超时（取不到就立刻回退，不让用户干等）
const REMOTE_COOLDOWN_MS = 30000 // 熔断：远端刚失败过就 30 秒内不再尝试（否则离线时每个资源都要等 2.5s）
let remoteDownUntil = 0

const CACHE_DIR = () => path.join(app.getPath('userData'), 'webcache')

function guessMime(urlPath) {
  return MIME[path.extname(urlPath.split('?')[0]).toLowerCase()] || 'application/octet-stream'
}

/** 把 URL 路径映射到缓存文件路径（防目录穿越） */
function cachePathFor(urlPath) {
  const rel = decodeURIComponent(urlPath.split('?')[0]).replace(/^[/\\]+/, '') || 'index.html'
  const safe = path.normalize(rel).replace(/^(\.\.[/\\])+/, '')
  const file = path.join(CACHE_DIR(), safe)
  return file.startsWith(CACHE_DIR()) ? file : null
}

function readCache(urlPath) {
  try {
    const file = cachePathFor(urlPath)
    return file && fs.existsSync(file) ? fs.readFileSync(file) : null
  } catch {
    return null
  }
}

function writeCache(urlPath, buffer) {
  try {
    const file = cachePathFor(urlPath)
    if (!file) return
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(file, buffer)
  } catch {
    /* 缓存失败不影响本次响应 */
  }
}

/** 从服务器取一个静态资源；失败/超时/非 2xx 返回 null */
function fetchRemote(urlPath) {
  return new Promise((resolve) => {
    let target
    try {
      target = new URL(urlPath, config.serverUrl)
    } catch {
      return resolve(null)
    }
    const transport = target.protocol === 'https:' ? https : http
    const req = transport.request(
      {
        protocol: target.protocol,
        hostname: target.hostname,
        port: target.port || (target.protocol === 'https:' ? 443 : 80),
        path: target.pathname + target.search,
        method: 'GET',
        timeout: REMOTE_TIMEOUT_MS,
        headers: { host: target.host, accept: '*/*' }
      },
      (res) => {
        const chunks = []
        res.on('data', (chunk) => chunks.push(chunk))
        res.on('end', () =>
          resolve({ status: res.statusCode || 0, contentType: res.headers['content-type'], body: Buffer.concat(chunks) })
        )
      }
    )
    req.on('timeout', () => {
      req.destroy()
      resolve(null)
    })
    req.on('error', () => resolve(null))
    req.end()
  })
}

/** 只发安装包里的副本（SPA history 回退到 index.html） */
function serveBundled(urlPath, res, source = 'bundled') {
  const safePath = path.normalize(urlPath).replace(/^([/\\])+/, '').replace(/^(\.\.[/\\])+/, '')
  let filePath = path.join(DIST_DIR, safePath)
  if (!filePath.startsWith(DIST_DIR)) {
    res.writeHead(403)
    res.end()
    return
  }
  if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
    const ext = path.extname(filePath).toLowerCase()
    if (ext && ext !== '.html') {
      // 资源类路径（.js/.css/图片…）缺失 → 直接 404。
      // 绝不能把 index.html 当 JS/CSS 发出去（浏览器会报 MIME/语法错误，页面白屏）。
      res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' })
      res.end('404')
      return
    }
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
    res.writeHead(200, {
      'content-type': MIME[path.extname(filePath).toLowerCase()] || 'application/octet-stream',
      'x-logiops-source': source
    })
    res.end(data)
  })
}

async function handleStatic(req, res) {
  const urlPath = (req.url || '/').split('?')[0]

  if (config.mode === 'remote-first' && Date.now() >= remoteDownUntil) {
    const remote = await fetchRemote(req.url)
    if (remote && remote.status >= 200 && remote.status < 300) {
      writeCache(urlPath, remote.body)
      res.writeHead(200, {
        'content-type': remote.contentType || guessMime(urlPath),
        'x-logiops-source': 'remote'
      })
      res.end(remote.body)
      return
    }
    if (Date.now() >= remoteDownUntil) {
      remoteDownUntil = Date.now() + REMOTE_COOLDOWN_MS
      log(`远端取页面失败（${urlPath}），${REMOTE_COOLDOWN_MS / 1000} 秒内改用本地缓存/包内副本`)
    }
  }

  const cached = readCache(urlPath)
  if (cached) {
    res.writeHead(200, { 'content-type': guessMime(urlPath), 'x-logiops-source': 'cache' })
    res.end(cached)
    return
  }

  serveBundled(urlPath, res, 'bundled')
}

function startLocalServer() {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      if (isApiPath((req.url || '').split('?')[0])) proxyToServer(req, res)
      else handleStatic(req, res).catch((error) => {
        log(`页面请求处理失败：${error && error.message}`)
        res.writeHead(500, { 'content-type': 'text/plain; charset=utf-8' })
        res.end('500')
      })
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
  mainWindow.once('ready-to-show', () => {
    bootLog('ready-to-show → 显示窗口')
    mainWindow.show()
    mainWindow.focus()
  })
  // 兜底：ready-to-show 在某些环境不会触发（渲染未完成/被代理拦等），
  // 那样窗口会永远隐藏、用户看到的是"双击了没反应"。这里 4 秒后强制显示。
  const showFallback = setTimeout(() => {
    if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.isVisible()) {
      bootLog('ready-to-show 未触发 → 强制显示窗口（界面可能是空白，请看 did-fail-load 日志）')
      mainWindow.show()
    }
  }, 4000)
  mainWindow.once('closed', () => {
    clearTimeout(showFallback)
    mainWindow = null
  })
  mainWindow.webContents.on('did-finish-load', () => bootLog('did-finish-load（页面已加载完成）'))
  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    bootLog(`did-fail-load code=${errorCode} desc=${errorDescription} url=${validatedURL}`)
  })
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    bootLog(`render-process-gone ${JSON.stringify(details)}`)
  })
  mainWindow.webContents.on('console-message', (_event, level, message) => {
    // 只记错误级（0=verbose 1=info 2=warning 3=error），避免刷屏
    if (level >= 3) bootLog(`renderer console error: ${message}`)
  })
  mainWindow.loadURL(`http://127.0.0.1:${localPort}/`)

  // 载入后探一次后端健康状态：连不上就用系统弹窗提示（比页面上的报错更清楚）
  mainWindow.webContents.once('did-finish-load', async () => {
    // 排障模式：自动把首屏截下来（触发方式见 captureRequested）
    if (captureRequested()) {
      bootLog('已启用自动截图，3 秒后保存首屏 PNG')
      setTimeout(() => {
        void captureWindow()
      }, 3000)
    }
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
  mode: config.mode,
  configPath: CONFIG_FILE(),
  cachePath: CACHE_DIR(),
  electron: process.versions.electron,
  chrome: process.versions.chrome
}))

/** 切换发布模式：写入配置 + 重载界面 */
function setPublishMode(mode) {
  if (!PUBLISH_MODES.includes(mode)) return
  config = { ...config, mode }
  remoteDownUntil = 0
  writeConfig(config)
  log(`发布模式切换为 ${mode}`)
  if (mainWindow) mainWindow.reload()
}

/**
 * 是否需要自动截图（排障用）：
 * - 环境变量 `LOGIOPS_CAPTURE_DIR` 指定目录，或 `LOGIOPS_CAPTURE=1`；
 * - 或者 userData 下存在标记文件 `capture-on-start.flag`（便于"双击 exe 也能截图"，
 *   不依赖命令行环境变量）。
 */
function captureRequested() {
  try {
    return Boolean(process.env.LOGIOPS_CAPTURE_DIR) ||
      process.env.LOGIOPS_CAPTURE === '1' ||
      fs.existsSync(path.join(app.getPath('userData'), 'capture-on-start.flag'))
  } catch {
    return false
  }
}

/**
 * 截取当前窗口内容存成 PNG。
 * 排障用：桌面软件"打开了但看不见/白屏"时，截图是唯一能远程确认它到底显示了什么的手段。
 */
async function captureWindow() {
  try {
    if (!mainWindow || mainWindow.isDestroyed()) return null
    const image = await mainWindow.webContents.capturePage()
    const dir = process.env.LOGIOPS_CAPTURE_DIR || app.getPath('userData')
    fs.mkdirSync(dir, { recursive: true })
    const file = path.join(dir, `window-${new Date().toISOString().replace(/[:.]/g, '-')}.png`)
    fs.writeFileSync(file, image.toPNG())
    log(`已保存界面截图：${file}`)
    bootLog(`截图已保存：${file}`)
    return file
  } catch (error) {
    bootLog(`截图失败：${error && error.message}`)
    return null
  }
}

/** 清空页面缓存（下次启动会重新从服务器拉） */
function clearPageCache() {
  try {
    fs.rmSync(CACHE_DIR(), { recursive: true, force: true })
    log('已清空页面缓存')
    dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: '页面缓存',
      message: '页面缓存已清空',
      detail: `目录：${CACHE_DIR()}\n下次打开会重新从服务器获取页面。`
    })
  } catch (error) {
    dialog.showErrorBox('清空缓存失败', String(error))
  }
}

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
              detail: `服务器地址：${config.serverUrl}\n发布模式：${config.mode}`
            })
          }
        },
        { type: 'separator' },
        {
          label: '发布模式',
          submenu: [
            {
              label: '远端优先（改前端只更新服务器）',
              type: 'radio',
              checked: config.mode === 'remote-first',
              click: () => setPublishMode('remote-first')
            },
            {
              label: '仅用本地副本（离线演示）',
              type: 'radio',
              checked: config.mode === 'local-only',
              click: () => setPublishMode('local-only')
            },
            { type: 'separator' },
            { label: '清空页面缓存…', click: clearPageCache }
          ]
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
          label: '保存界面截图…',
          click: async () => {
            const file = await captureWindow()
            dialog.showMessageBox(mainWindow, {
              type: file ? 'info' : 'error',
              title: '界面截图',
              message: file ? '截图已保存' : '截图失败',
              detail: file || '详情见 startup.log'
            })
          }
        },
        {
          label: '关于 LogiOps',
          click: () => {
            dialog.showMessageBox(mainWindow, {
              type: 'info',
              title: '关于',
              message: `LogiOps 桌面客户端 v${app.getVersion()}`,
              detail: [
                `后端服务器：${config.serverUrl}`,
                `发布模式：${config.mode === 'remote-first' ? '远端优先（页面来自服务器）' : '仅用本地副本'}`,
                `配置文件：${CONFIG_FILE()}`,
                `页面缓存：${CACHE_DIR()}`,
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
      log(`启动：serverUrl=${config.serverUrl} mode=${config.mode} version=${app.getVersion()}`)
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
