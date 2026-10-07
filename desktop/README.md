# LogiOps 桌面客户端（Windows）

把 LogiOps 打包成**可安装的 Windows 软件**：双击图标就能用，像正经客户端一样（安装包、桌面快捷方式、开始菜单、图标）。

## 架构：为什么它不是"单机软件"

```
┌─ Windows 桌面软件（Electron 外壳）──────────────────────────────┐
│  BrowserWindow ──► http://127.0.0.1:<随机端口>                   │
│      主进程起的本地小服务（相当于把 nginx 换成了 Node）：          │
│        ├─ 静态文件：随包携带的前端 dist（带 SPA history 回退）      │
│        └─ /api/*、/docs、/openapi.json ──反向代理──►  云服务器      │
└──────────────────────────────────────────────────────────────────┘
                        │
                        ▼
              云端 FastAPI + MySQL（所有客户端共享同一份数据）
```

**为什么这样设计（面试可讲）**

| 决策 | 原因 |
|---|---|
| 数据放在云端，不放本地 | 多人/多设备要改同一份数据 → 必须有集中存储；本地 SQLite 会变成"每人一份、互相看不见" |
| 本地起小服务而不是直接加载静态文件 | 前端仍只请求相对路径 `/api/v1`，**前端代码一行都不用改**，也没有跨域问题 |
| 服务器地址运行时可配 | 换 IP / 换域名不用重新打包；写进 `%APPDATA%\LogiOps\config.json` |
| 菜单里带"检测后端连接" | 演示前一眼看出是网络问题还是软件问题 |

## 使用

### 一键构建

```powershell
pwsh -File scripts/build-desktop.ps1
```

产物（`desktop/dist/`）：

| 产物 | 用途 |
|---|---|
| **`LogiOps-Setup-0.1.0.exe`** | 安装版（NSIS）：装到本机、桌面/开始菜单快捷方式 |
| **`LogiOps-Portable-0.1.0.exe`** | **绿色便携版**：单文件、双击即用、不安装、不写注册表（演示/U 盘首选） |
| `win-unpacked\LogiOps.exe` | 免安装目录版（调试用） |

> 安装版**装完不会自动启动**（`runAfterFinish: false`）。这是刻意的：装完自己起进程，会导致"再装时文件被占用"的经典冲突。
> 便携版没有安装过程，天然避开这个问题，演示时优先用它。

### 装不上/无法关闭怎么办

Windows 不允许覆盖**正在运行**的程序文件。如果安装时提示「LogiOps 无法关闭」：

1. 先退出正在运行的 LogiOps（任务管理器 → **「详细信息」**标签页 → `LogiOps.exe` → 结束任务；它可能没有可见窗口）
2. 或直接改用**便携版**，或重启电脑后再装

### 出问题时看日志

启动日志写在 `%APPDATA%\LogiOps\startup.log`（含启动失败原因）。
程序对启动异常做了**fail-fast**：启动失败会弹错并退出 —— 不会留下"没有窗口却还活着、还锁着文件"的僵尸进程。

### 免安装直接运行（调试用）

```powershell
desktop\dist\win-unpacked\LogiOps.exe
```

### 开发模式（改完代码立刻看效果）

```powershell
corepack pnpm --dir frontend run build          # 先构建前端
Copy-Item -Recurse -Force frontend\dist desktop\web
corepack pnpm --dir desktop install
corepack pnpm --dir desktop start               # 拉起 Electron
```

## 用软件改服务器地址

菜单 **服务器 → 设置服务器地址…**（快捷键 `Ctrl+,`）→ 填 `http://<公网IP>` 或域名 → 保存后自动重新加载。

- 配置文件：`%APPDATA%\LogiOps\config.json`（也可用菜单「打开配置文件」直接编辑）
- 默认值：`http://101.200.139.115`（`main.cjs` 顶部 `DEFAULT_SERVER`，换服务器就改这里再重新打包）
- 团队协作：所有人填**同一个**服务器地址，看到的就是同一份数据

## 打包环境注意（国内网络）

| 坑 | 处理 |
|---|---|
| `electron` / builder 二进制下载慢 | `desktop/.npmrc` 已指向 npmmirror 镜像；脚本里也设了 `ELECTRON_MIRROR` |
| `ERR_PNPM_IGNORED_BUILDS`（electron-winstaller） | pnpm 12 的构建白名单在 **`desktop/pnpm-workspace.yaml`** 的 `allowBuilds`（已配好，别删） |
| electron 运行时没下下来（`node_modules/electron/dist` 为空） | `cd desktop\node_modules\electron; node install.js` |
| `No JSON content found in output` | electron-builder 会直接调用 `pnpm`，若本机只有 corepack 提供的 pnpm（不在 PATH）就会这样 → 一键脚本会自动造一个 `pnpm.cmd` shim |
| 安装包体积 100 MB+ | Electron 自带 Chromium，属正常；想小体积可换 Tauri（Rust，约 5 MB） |

## 与整站部署的关系

| 你想给别人的东西 | 用什么 |
|---|---|
| "打开网址就能用" | 昨天的云服务器部署（`docs/10`） |
| "像软件一样装到电脑上" | 本文的桌面客户端 |
| 两者数据一致 | 是的：桌面端连的就是同一台服务器、同一个库 |
