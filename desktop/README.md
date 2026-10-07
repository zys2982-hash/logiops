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

### 一键构建安装包

```powershell
pwsh -File scripts/build-desktop.ps1
```
产物：`desktop/dist/LogiOps-Setup-<版本>.exe`（NSIS 安装包）。

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
