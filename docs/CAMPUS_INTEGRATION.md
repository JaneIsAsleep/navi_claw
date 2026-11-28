# Navi 校园平台集成说明

更新时间：2026-11-28

本文档用于维护 Navi 与南方科技大学教学管理与服务平台（TIS）和 Blackboard（BB）的集成。后续合并分支或升级依赖时，应先依据本文档检查接口契约，再进行修改。

## 1. 范围和约束

- 校园登录使用真实南科大 CAS。
- 允许访问的宿主仅为：
  - `tis.sustech.edu.cn`
  - `bb.sustech.edu.cn`
  - `cas.sustech.edu.cn`
- CAS 存在滑块和二维码验证。账号密码直连登录不是主要登录方式。
- Navi 页面与 TIS、BB 属于不同域，普通网页不能读取它们的 HttpOnly Cookie。
- 当前可用登录方式是单个独立 Edge 窗口：
  - CAS 和 TIS 在窗口中完成登录。
  - 同一个窗口随后跳转 BB 完成 SSO。
  - 登录成功后窗口最小化，浏览器上下文继续供 Navi 读取。
- 不记录 CAS 密码。
- Cookie 和平台页面数据不得进入日志、Git、截图或共享证据。
- 本机会话只写入被忽略的 `data/` 目录，并使用 Windows DPAPI 加密。

## 2. 代码结构

| 文件 | 职责 |
|---|---|
| `backend/navi/campus.py` | CAS/BB 登录、浏览器生命周期、Cookie 会话、原始页面读取 |
| `backend/navi/campus_dashboard.py` | 从 BB 公告和日历提取消息、DDL，执行日期过滤和去重 |
| `backend/navi/campus_summary.py` | 构造模型输入、解析模型 JSON、合并条目摘要 |
| `backend/navi/gateway.py` | 校园看板模型整理、1 小时缓存、后台更新 |
| `backend/navi/web.py` | 校园 HTTP API |
| `backend/navi/agent.py` | 对话时可调用的校园工具 |
| `frontend/src/CampusPanel.tsx` | 校园页、登录状态、刷新和缓存感知 |
| `frontend/src/data.ts` | 校园 API TypeScript 契约 |
| `engineering/verification/tests/test_campus*.py` | 校园登录、看板、摘要测试 |

## 3. 数据流

```text
Navi Web / CLI / TUI / Agent
            |
            v
         Gateway
            |
            +--> 1 小时最终看板缓存
            |
            v
       CampusService
            |
            +--> CAS 登录窗口
            +--> TIS / Blackboard Cookie
            |
            v
    Playwright Edge Context
            |
            +--> Blackboard 公告
            +--> Blackboard 日历
            |
            v
  campus_dashboard.py 结构化提取
            |
            v
  campus_summary.py 模型摘要
```

## 4. 登录与会话

### 4.1 启动独立登录窗口

调用：

```http
POST /api/campus/browser-login
Content-Type: application/json

{}
```

行为：

1. 使用单实例锁，已存在登录任务时直接复用。
2. 已有有效会话和加密 Cookie 时直接返回成功，不创建窗口。
3. 启动一个可见 Edge 窗口。
4. 打开 `https://tis.sustech.edu.cn/cas`。
5. 用户在真实 CAS 页面完成账号密码、滑块或二维码验证。
6. CAS 跳回 TIS，记录 TIS Cookie 和 localStorage。
7. 同一窗口跳转 BB SSO，建立 BB Cookie。
8. 保存 URL、Cookie 和浏览器存储。
9. 登录窗口最小化，浏览器上下文保留给校园读取。

当前登录阶段：

| phase | 含义 |
|---|---|
| `opening` | 正在准备登录 |
| `cas` | 等待 CAS 登录 |
| `blackboard` | TIS 成功，正在建立 BB 会话 |
| `validating` | 正在校验和保存会话 |

状态查询：

```http
GET /api/campus/browser-login/status
```

成功响应示例：

```json
{
  "status": "succeeded",
  "phase": "",
  "error": "",
  "latest_access": "2026-11-28T09:21:56+00:00"
}
```

### 4.2 关键实现约束

- 登录任务必须由异步 FastAPI 路由启动，不能在同步函数中调用 `asyncio.create_task`。
- 浏览器登录必须有 `asyncio.Lock`，防止重复窗口。
- CAS 登录期间可临时监听新页面并关闭额外弹窗。
- 该监听器必须在登录成功后移除。否则后续 BB/TIS 页面会被当成弹窗关闭，产生 `ERR_ABORTED`。
- 登录上下文保留后，后续读取应优先复用该上下文。
- 当前独立窗口登录流程会真实经过 BB 页面。不要改成仅后台请求，除非重新完整验证 BB Cookie 和页面状态。

### 4.3 加密会话

会话文件：

```text
data/.campus-session
data/campus-profile.json
```

不要读取后输出 Cookie 值。

会话结构版本为 `2`，包含：

- Playwright `cookies`
- Playwright `origins`
- localStorage
- sessionStorage

Windows 下使用 DPAPI。允许排查的字段仅限：

- Cookie 名称
- Cookie 域名
- Cookie 路径
- Cookie 是否过期
- localStorage 键名

## 5. HTTP API

所有接口只接受：

- `127.0.0.1`
- `localhost`
- 测试环境 `testserver`

POST、PUT、PATCH、DELETE 必须使用 `application/json`。

### 5.1 登录状态

```http
GET /api/campus/status
```

响应：

```json
{
  "logged_in": true,
  "services": [
    {"id": "tis", "name": "教学管理与服务平台"},
    {"id": "bb", "name": "Blackboard 课程平台"}
  ]
}
```

### 5.2 独立浏览器登录

```http
POST /api/campus/browser-login
Content-Type: application/json

{}
```

### 5.3 低层账号密码登录

```http
POST /api/campus/login
Content-Type: application/json

{
  "username": "student-id",
  "password": "not-logged"
}
```

该接口仅保留为低层兼容路径。当前 CAS 通常要求人工验证，前端不提供此入口。密码不得写入日志或文件。

### 5.4 退出

```http
POST /api/campus/logout
Content-Type: application/json

{}
```

行为：

- 取消正在运行的登录任务。
- 删除加密会话和登录标记。
- 关闭专用浏览器上下文。
- 清除最终看板缓存。

### 5.5 原始首页读取

```http
GET /api/campus/overview?service=bb&refresh=false
```

`service` 可取 `bb` 或 `tis`。默认 `bb`。

返回页面标题、正文、截断状态和站内链接。该接口主要用于兼容旧页面和 Agent，校园主界面使用看板接口。

### 5.6 校园看板

```http
GET /api/campus/dashboard?refresh=false
```

参数：

| 参数 | 说明 |
|---|---|
| `refresh=false` | 优先使用 1 小时缓存；过期时返回旧缓存并后台更新 |
| `refresh=true` | 跳过缓存，立即重新读取 BB 并调用模型 |

响应核心字段：

```json
{
  "logged_in": true,
  "generated_at": 1780000000.0,
  "cached": true,
  "stale": false,
  "cached_at": 1780000000.0,
  "cache_expires_at": 1780003600.0,
  "refresh_scheduled": false,
  "model_generated": true,
  "brief": "模型生成的一句话总览",
  "summary": {
    "courses": 3,
    "messages": 6,
    "deadlines": 4
  },
  "recent_messages": [],
  "deadlines": []
}
```

缓存过期时：

```json
{
  "cached": true,
  "stale": true,
  "refresh_scheduled": true
}
```

后台更新失败时：

```json
{
  "cached": true,
  "stale": true,
  "refresh_scheduled": false,
  "refresh_error": "错误摘要"
}
```

### 5.7 读取指定校园页面

```http
POST /api/campus/page
Content-Type: application/json

{
  "service": "bb",
  "url": "https://bb.sustech.edu.cn/...",
  "refresh": false
}
```

限制：

- 只允许 TIS 和 BB 的 HTTPS 地址。
- 默认正文上限为 `PAGE_TEXT_LIMIT`。
- 返回 `text_length` 和 `truncated`。
- 不返回密码或 Cookie。

## 6. 看板提取规则

### 6.1 Blackboard 输入

- 公告：最多读取当前列表前 40 条 `#announcementList > li.clearfix`。
- 日历：读取 `.fc-event`。
- 页面正文只用于本地结构化处理。

### 6.2 时间规则

- 无年份日期优先使用课程名称中的年份，其次使用公告发布年份。
- 已过期事项不进入 DDL。
- 公告截止时间只接受当前日期之后至未来 90 天。
- 日历事件同样只接受未来 90 天。
- 同标题、同日期的事项会合并。
- 带 `postponed` 的延期事项只保留最新截止时间。

### 6.3 输出规则

- `recent_messages` 最多 6 条。
- `deadlines` 最多 8 条。
- 作业和考试不单独成区，统一归入 `deadlines`。
- 不再返回 `assignments` 和 `exams` 字段。
- 无稳定详情 URL 的公告不生成假链接。
- `url` 为空时前端不得展示链接。

## 7. 模型摘要

实现文件：

```text
backend/navi/campus_summary.py
```

规则：

- 使用当前 Gateway 配置的模型和 API 密钥。
- 每个独立事项保留原始 ID。
- 模型负责生成：
  - 一条 `brief`
  - 每个条目的简短任务摘要
- 模型不得修改日期、标题、URL 或增加新事项。
- 输入按最多 2 个事项分批，最多 3 批并发。
- 成功批次的摘要会合并；失败批次保留确定性摘要。
- 全部批次失败时，接口返回错误，不伪造模型结果。
- 同一公告对应的多个 DDL 只摘要一次，然后复用到关联 DDL。

修改模型调用时必须保留：

- 模型调用用量记录。
- 每日预算检查。
- JSON 格式校验。
- 不允许模型输出直接覆盖日期和链接。

## 8. 缓存和自动更新

最终看板由 `Gateway` 缓存，不使用 `CampusService.dashboard()` 的 30 秒原始缓存作为前端主缓存。

常量：

```text
CAMPUS_DASHBOARD_CACHE_SECONDS = 3600
CAMPUS_DASHBOARD_RETRY_SECONDS = 300
```

流程：

1. 一小时内请求直接返回缓存。
2. 超过一小时后，先返回旧缓存，标记 `stale=true`。
3. 同时启动一个后台更新任务。
4. 页面前台每 5 分钟检查一次缓存。
5. 发现过期后，每 5 秒重新读取一次，最多 8 次。
6. 更新成功后自动替换页面数据。
7. 更新失败保留旧内容，5 分钟内不重复调用模型。
8. 手动刷新使用 `refresh=true`，立即更新。
9. 登录、退出和切换账号会清空缓存。
10. 缓存只存在内存中，重启 Navi 后清空。

不要在缓存过期时阻塞用户等待模型。必须保持 stale-while-revalidate 行为。

## 9. Agent 工具

`backend/navi/agent.py` 中保留：

| 工具 | 用途 |
|---|---|
| `get_campus_dashboard` | 读取结构化近期消息和 DDL，默认用于校园问题 |
| `get_campus_overview` | 读取 BB 或 TIS 原始首页；默认 BB |
| `read_campus_page` | 读取允许域名下的具体页面 |

校园工具只在聊天作业中提供。不要在非聊天后台作业中暴露。

## 10. 前端契约

TypeScript 类型位于：

```text
frontend/src/data.ts
```

关键约束：

- 校园主界面使用 `CampusDashboard`。
- `CampusDashboard` 不包含 `assignments` 或 `exams`。
- `stale` 必须触发后台更新轮询。
- `cached_at` 用于显示上次更新时间。
- `refresh_error` 用于显示缓存更新失败状态。
- 登录按钮只调用独立浏览器登录，不采集 CAS 密码。

前端刷新策略：

- 首次进入校园页：请求 `/campus/dashboard?refresh=false`
- 页面可见：每 5 分钟检查缓存
- `stale=true`：每 5 秒检查后台结果
- 手动刷新：`refresh=true`
- 登录成功：强制刷新

## 11. 调试

日志：

```text
data/campus-debug.log
data/gateway.log
```

允许记录：

- 平台名称
- 最终 URL 的 host 和 path
- Cookie 名称
- 页面阶段
- 异常类型

禁止记录：

- 密码
- Cookie 值
- CAS ticket
- 完整聊天内容
- 未脱敏页面正文

常见错误：

| 错误 | 原因 | 处理 |
|---|---|---|
| `ERR_ABORTED` | 页面被错误关闭或登录监听器残留 | 检查登录后是否移除临时 `page` 监听器 |
| `EADDRINUSE` | 已有 Gateway 占用端口 | 使用本项目 status/stop 入口 |
| CAS 验证无限等待 | 人工滑块未完成 | 重新打开独立登录窗口 |
| `Text file busy` | 启动脚本或包装器正在覆盖自身 | 检查 launcher 和 Windows 包装脚本 |
| 纯文本 `Internal Server Error` | API 未捕获异常 | 查看 `gateway.log`，前端应兼容非 JSON 错误 |

## 12. 验证

定向测试：

```powershell
.\.venv-win\Scripts\python.exe -m pytest `
  engineering/verification/tests/test_campus.py `
  engineering/verification/tests/test_campus_dashboard.py `
  engineering/verification/tests/test_campus_summary.py `
  engineering/verification/tests/test_gateway.py -q
```

全量测试：

```powershell
.\.venv-win\Scripts\python.exe -m pytest -q
```

前端：

```powershell
npm --prefix frontend run build
```

测试必须覆盖：

- Cookie 和 localStorage 加密往返
- 登录单实例锁
- 公告和日历日期过滤
- 过期 DDL 不出现
- 延期和同日事项去重
- 模型摘要合并
- 1 小时缓存
- stale-while-revalidate
- 手动强制刷新

## 13. 分支合并适配清单

合并其他分支前：

1. 备份当前未提交改动。
2. 检查 `origin/codex/navi-edit-approval` 是否修改：
   - `backend/navi/gateway.py`
   - `backend/navi/web.py`
   - `frontend/src/data.ts`
   - `frontend/src/App.tsx`
   - `frontend/src/App.module.css`
3. 检查远端是否修改 API 路由注册方式。
4. 检查远端是否修改 `Gateway.chat()`、模型调用、用量记录或缓存字段。
5. 检查远端是否修改前端 `api()` 或页面导航。

合并后：

1. 确认所有 `/api/campus/*` 路由仍注册。
2. 确认浏览器登录仍然只有一个窗口。
3. 确认登录后的 `page` 监听器已移除。
4. 确认 BB SSO 能完成并保存 Cookie。
5. 确认 `CampusDashboard` 字段与前端类型一致。
6. 确认 `assignments` 和 `exams` 没有重新出现。
7. 确认缓存常量为 3600 秒。
8. 确认缓存过期时先返回旧数据。
9. 确认模型摘要失败不会破坏上一版缓存。
10. 确认 CLI `campus` 和 TUI 校园页仍然可用。
11. 运行校园定向测试。
12. 运行全量测试和前端构建。
13. 使用真实账号完成一次 CAS 登录。
14. 实际请求 `/api/campus/dashboard?refresh=true`。
15. 验证没有 Cookie 值、密码或未脱敏页面正文进入日志。

## 14. 示例请求

PowerShell：

```powershell
Invoke-RestMethod `
  -Uri 'http://127.0.0.1:8766/api/campus/dashboard?refresh=false' `
  -Method Get
```

强制刷新：

```powershell
Invoke-RestMethod `
  -Uri 'http://127.0.0.1:8766/api/campus/dashboard?refresh=true' `
  -Method Get `
  -TimeoutSec 180
```

CLI：

```powershell
.\navi-windows.cmd campus
.\navi-windows.cmd campus --refresh
```

Linux/WSL：

```bash
./navi campus
./navi campus --refresh
```
