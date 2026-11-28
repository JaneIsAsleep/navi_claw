# Navi 命令行

在项目目录运行 `./start.command --no-browser` 启动 Gateway。用 `./navi --help` 查看命令，用 `./navi settings set --help` 或 `./navi goal --help` 查看子命令和参数。CLI、Web 和全屏终端界面使用同一个 Gateway 和数据库。

## 配置、校园和课程演示

```sh
./navi settings set --set-key --notification-policy strict
./navi settings check --wait
./navi settings set --workflow deadline_briefing --daily-requests 100
./navi settings usage
./navi campus
./navi campus --refresh
./navi check-items add '提交课程简报'
./navi schedule '定期检查' '检查关注事项并提醒我' --kind heartbeat --interval 3600 --in 0
./navi jobs
./navi actions
```

`settings set` 支持修改模型、协议、自定义 Endpoint、时区、工作流、通知策略、调用限额和费用字段。保存密钥使用 `--set-key` 或 `--set-search-key`，两者都通过隐藏输入读取。新增自定义 Endpoint 同时提供 `--messages-url` 和 `--completions-url`。切换用 `--endpoint`，传 `official` 可切回默认地址。真实连接测试及原生搜索测试使用配置的服务额度。

`campus` 返回与 Web 校园页共用的近期消息、临近 DDL 和模型整理结果。默认读取一小时缓存；缓存过期时先返回旧内容并在后台更新，`--refresh` 会立即重新读取 Blackboard 并整理。使用前需在 Web 校园页完成 CAS 登录。

## 对话、提醒和审批

| 操作 | 命令 |
|---|---|
| 对话、流式输出 | `chat '消息' --new-session --stream`，实时增量写到标准错误，最终作业 JSON 写到标准输出 |
| 搜索历史 | `search-sessions '关键词' --all --limit 20 --offset 0`，按标题和正文检索，包含封存话题 |
| 继续对话并引用已有条目 | `chat '讨论下一步' --session "$SESSION_ID" --reference "$RECORD_ID" --wait`，可重复指定最多五条引用 |
| 查看、标记已读、清空主对话 | `session "$SESSION_ID"`、`read-session "$SESSION_ID"`、`reset-main --yes` |
| 安排、调整提醒 | `schedule` 支持 `--at`、`--in`、`--goal`、`--calendar-timezone`，`edit-schedule` 支持修改标题、内容、间隔和触发时间 |
| 确认收到、清理列表 | `notifications`、`acknowledge "$OUTBOX_ID"`、`dismiss-reminder "$ACTION_ID"`、`schedule-history "$SCHEDULE_ID"`、`complete-schedule "$SCHEDULE_ID"`、`remove-schedule "$SCHEDULE_ID"` |
| 修改及处理审批 | `edit-action "$ACTION_ID" --body '新内容'`、`action-history "$ACTION_ID"`、`approve "$ACTION_ID" --hash "$HASH"`、`reject "$ACTION_ID" --hash "$HASH"` |
| 查看运行轨迹 | `jobs`、`job "$JOB_ID" --stream`、`evidence "$JOB_ID"`、`audit --job "$JOB_ID"` |

提案修改后使用新版本的 `content_hash`。目标或提醒的对话修改提案若需调整，先拒绝旧提案，再从对话发起新修改。目标或提醒详情命令与 Web 管理页具有相同的直接编辑行为。

## 目标、记忆和资料

| 功能 | 子命令 |
|---|---|
| 目标 | `goal list/show/add/edit/pause/resume/complete/reactivate/delete` |
| 目标引导 | `goal start career --wait`，再从 `messages` 的 `interaction` 取卡片 ID、选项 ID 和 `setup_version`，交给 `goal choose` |
| 记忆 | `memory list/show/add/edit/enable/disable/delete` |
| 构想 | `ideas --wait` 生成构想，`idea list/show/start` 可查看或开始已有构想 |
| 成果 | `artifact list/show/download`。用 `artifact download "$ARTIFACT_ID" -o report.md` 保存到新文件，用 `-o -` 输出原始字节 |
| 动态 | `feed list/show/brief/refresh/generate`。使用 `feed list --before "$CURSOR"` 读取下一页 |
| 订阅 | `feed sources/follow/preview/add-source/toggle-source`。先预览公开订阅 URL，再用 `add-source` 添加 |
| 本机关注事项 | `check-items list/add/edit/delete/replace`。`replace` 接收 JSON 文件，也可用 `-` 从标准输入读取 |

命令中的 ID、游标和哈希取自对应列表或工单回执。普通命令输出 JSON，可用 shell 工具处理。

Web 提供内联图片预览、Markdown 排版、双栏讨论、浏览器草稿及滚动位置恢复，还有点击式审批和目标引导。CLI 对应操作使用 JSON 和显式参数。动态里已有的来源链接随记录返回，图片预览仍在 Web。图片代理接口没有独立 CLI 命令。
