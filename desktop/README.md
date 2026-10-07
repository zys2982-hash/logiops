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

### ⚠️ 不要从"仓库/工作区目录"直接双击运行构建产物

**2026-10-07 真机实测（重要）**：同一个 exe，

| 运行位置 | 结果 |
|---|---|
| 普通目录（如 `%LOCALAPPDATA%\Programs\LogiOps`、`D:\LogiOps`） | ✅ 正常打开 |
| **DSH/沙箱加固过 ACL 的目录**（如 `D:\workspace1\...`，多出 `CodexSandboxUsers` + Deny 规则） | ❌ `render-process-gone: launch-failed` → 界面出不来，主进程随后崩溃（`0x80000003`） |

原因：Chromium 启动**渲染进程**要用到沙箱/受限令牌机制，而这类目录的 ACL 由沙箱写入
（`CodexSandboxUsers`、`Everyone: Deny DeleteSubdirectoriesAndFiles`），导致渲染进程创建失败。

**正确做法**：构建完（`scripts/build-desktop.ps1`）后，用下面这条把它"就地安装"到用户目录，
再从桌面/开始菜单快捷方式打开：

```powershell
pwsh -File scripts/deploy-desktop-local.ps1          # 用现有构建产物
pwsh -File scripts/deploy-desktop-local.ps1 -Build   # 先重新打包再安装
```

（它只做两件事：复制到 `%LOCALAPPDATA%\Programs\LogiOps`、创建快捷方式 —— 不需要管理员、不写注册表。）

**排障三件套**（这套东西就是为此加的）：
`%APPDATA%\LogiOps\boot.log`（启动打点）、`startup.log`（业务日志）、`window-*.png`（首屏截图，
放 `capture-on-start.flag` 即自动截图）。

### 装不上/无法关闭怎么办

Windows 不允许覆盖**正在运行**的程序文件。如果安装时提示「LogiOps 无法关闭」：

1. 先退出正在运行的 LogiOps（任务管理器 → **「详细信息」**标签页 → `LogiOps.exe` → 结束任务；它可能没有可见窗口）
2. 或直接改用**便携版**，或重启电脑后再装

> 若上次安装拉起的实例是**管理员权限**，安装程序的自动 `taskkill` 会被拒绝，于是转成"请你手动关闭"（源码见 `app-builder-lib/templates/nsis/include/allowOnlyOneInstallerInstance.nsh`）。

### 提示「不能打开要写入的文件: D:\LogiOps\Uninstall LogiOps.exe」

**安装目录的权限问题**：如果上一次安装是**提权（管理员）运行**的，自定义目录会被"接管"——
2026-10-07 实测：`D:\LogiOps` 属主变成 `BUILTIN\Administrators`，普通用户只有读取权限，
于是再装（普通权限）时写不进卸载程序。

处理（任选）：
1. **推荐**：重新安装时把安装位置改回默认的 `%LOCALAPPDATA%\Programs\LogiOps`（用户目录，一定有写权限）
2. 右键安装包 → **以管理员身份运行**（能装上，但以后更新/卸载可能还要提权）
3. 清理旧目录：用**管理员** PowerShell 执行 `Remove-Item D:\LogiOps -Recurse -Force`

**结论**：别把程序装到"需要管理员权限才能写"的目录；用默认安装路径最省事。

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

## 发布模式：改前端要不要重新打包？（B+ 方案）

菜单 **服务器 → 发布模式**：

| 模式 | 页面从哪来 | 改了前端要做什么 | 断网/服务器挂时 |
|---|---|---|---|
| **远端优先**（默认） | 服务器的 nginx | **只更新服务器**，客户端刷新即最新 ✓ | 自动逐级回退，界面照样能开 |
| 仅用本地副本 | 安装包内（`app.asar`） | 重新打包 + 重新分发 | 完全可用（但看到的是打包时的版本） |

**三级回退**（远端优先模式下）：

```
① 从服务器取（2.5s 超时）──成功──► 同时写入本地缓存 ──► 返回
                          └─失败/404─► ② 本地缓存 %APPDATA%\LogiOps\webcache
                                          └─没有─► ③ 安装包内副本 app.asar/web
```

配套细节：

- **断网熔断**：远端连续失败后 30 秒内不再尝试（否则离线时每个资源都要等 2.5 秒超时，页面就卡死了）
- **缓存目录**：`%APPDATA%\LogiOps\webcache`，菜单里可一键「清空页面缓存…」
- **来源可查**：每个响应带 `x-logiops-source: remote | cache | bundled`（开发者工具 → Network 里能看到）
- **安全**：资源类路径（`.js/.css/图片`）取不到时返回 **404**，不会把 `index.html` 当 JS 发出去

> 这样"更新敏捷度"和"离线可用性"两个目标同时满足：日常改页面只需在服务器上更新一次；演示现场断网也不会白屏。

## 与整站部署的关系

| 你想给别人的东西 | 用什么 |
|---|---|
| "打开网址就能用" | 昨天的云服务器部署（`docs/10`） |
| "像软件一样装到电脑上" | 本文的桌面客户端 |
| 两者数据一致 | 是的：桌面端连的就是同一台服务器、同一个库 |
