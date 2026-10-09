# Navi

本机个人 AI 助理。聊天、记忆、目标、动态、建议与成果共用一个持久 Gateway。CLI 和 Web 操作相同状态。

项目根目录为当前克隆的 `navi_claw` 文件夹。本目录与其他 OOAD 项目完全独立。

## 开始使用

当前机器已准备依赖和前端。双击 `start.command`，就绪后自动打开 [Navi](http://127.0.0.1:8766)。重复启动复用已有后台。

启动完成后，启动终端窗口可以关闭。关闭浏览器也不会停止任务。真正停止请双击 `stop.command`；查看状态、入口和日志请双击 `status.command`。

使用 `./run` 会在前台运行，需要保留终端，按 `Ctrl+C` 正常停止。请先停止后台再使用前台入口。

重启保留会话、记忆、目标、调度、作业、审批、消息和成果。电脑休眠或停止后台期间不执行检查，恢复后每个调度最多补做一次最近错过的检查，不补跑全部周期。

新机器首次准备需要 Python 3.12、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 [Node.js](https://nodejs.org/en/download) 22.12 或更高版本。在项目目录执行 `./setup`，再双击启动。Python 与前端分别由 `uv.lock` 和 `frontend/package-lock.json` 锁定依赖。

Windows 组员使用 WSL2 的 Ubuntu 运行当前版本。后端使用 Unix 文件锁，启动入口也是 shell 脚本；当前没有原生 Windows 运行验证。在管理员 PowerShell 中执行以下命令，按提示重启并完成 Ubuntu 首次配置，见 [Microsoft 安装说明](https://learn.microsoft.com/en-us/windows/wsl/install)。

```powershell
wsl --install -d Ubuntu
wsl --list --verbose
```

确认 Ubuntu 的 VERSION 为 2。以下命令均在 Ubuntu 终端执行，先接受 GitHub 私有仓库邀请并完成 Git 登录。在 Ubuntu 内按 [Node.js 官网](https://nodejs.org/en/download) 安装 Linux 版本 Node.js 22.12 或更高版本，使 `node --version` 和 `npm --version` 可用；Python 3.12 由 `uv` 准备。

```sh
sudo apt update
sudo apt install -y git curl ca-certificates
curl -LsSf https://astral.sh/uv/install.sh | sh
. "$HOME/.local/bin/env"
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/JaneIsAsleep/navi_claw.git
cd navi_claw
./setup
./test
./start.command
```

在 Windows 浏览器打开 [Navi](http://localhost:8766)，首次使用在设置页填写自己的 DeepSeek 密钥。[Microsoft 的 WSL 网络说明](https://learn.microsoft.com/en-us/windows/wsl/networking) 支持从 Windows 浏览器通过 localhost 访问 Linux 服务。本项目的 Windows/WSL2 实机验收仍需组员首次安装时完成；WSL 退出、Windows 休眠或关机期间无法执行后台任务。无需双击 `.command` 文件，在 Ubuntu 终端执行 `./stop.command`、`./status.command` 即可。

仓库共享源码、锁定依赖、工程资料与验证代码。个人密钥、数据库、聊天状态和原始私人文本证据留在忽略版本控制的 `data/` 中；截图材料按用户确认共享。`engineering/requirements/修改队列.xlsx` 含敏感截图，只保存在维护者本机，克隆仓库不会获得该文件；对应需求见计划与已共享证据。每位组员独立创建本机状态，完整备份仅用于本机恢复。历史日志中的旧提交号保存在维护者本机归档分支，小组仓库从脱敏后的开发基线开始；旧阶段的验收材料仍随仓库保留。开发时从最新 `main` 创建功能分支，通过 Pull Request 汇合；提交前执行 `./test` 和 `npm --prefix frontend run build`。`./test --live` 会使用当前机器的密钥并消耗模型额度。

更新前先保存自己分支的工作。回到 `main` 后执行 `./stop.command`、`git pull --ff-only`、`./setup`、`./test`、`./start.command`，使后端和前端加载更新后的版本。

在[本机设置页](http://127.0.0.1:8766/#settings)填写 DeepSeek 密钥，无需发到聊天里。本次真实验证使用 `deepseek-flash` 与 Messages 协议。模型名称可修改，只有实际连接检查成功才代表你的账户可用。接口参考 [DeepSeek 官方英文实现](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm-deepseek/README.md)。

对话和后台生成时，Navi 会把当前会话、摘要、有效记忆、进行中的目标、待办上下文及本次来源资料发送给你配置的 DeepSeek 服务。停用的记忆不参与后续回答。数据文件保存在本机；密钥位于忽略版本控制的 `data/.credentials.json`，权限为 `0600`，不会进入普通日志或证据。

首次进入唯一主对话，以后恢复最近的有效话题和草稿。主对话不可重命名、封存或删除。副对话在发出首条消息后进入历史，可从行内更多菜单管理；Web 搜索匹配标题和已保存消息正文，显示命中片段，包含封存话题。搜索及查看命中无需模型，打开历史不会自动解封。CLI 默认继续按标题检索。首轮成功后异步生成简短主题名，手动名称保留。永久删除会取消该话题的未完成提醒与未决动作，保留独立保存的目标、记忆、成果及必要执行记录。

可以从“记住：请用简洁中文回答我”开始。头像 → 身份信息 → 记忆提供逐条管理。目标分类先展示说明，点击“现在就开始”才创建话题并开始选项引导，关闭说明不写入数据。选项位于聊天气泡内，最多两次答复后展示可确认草案。确认保存由 Gateway 持久化，之后再补行动步骤或提醒。桌面聊天和目标可以并排或收起，窄屏切换保留草稿。勾选完成时立即结束关联工作，完成行约三秒后移入已完成列表；已完成列表支持恢复及移除目标，保留相关对话、文件和审计。恢复目标不会恢复旧提醒。头像 → 提醒事项设置单次、周期或 Heartbeat 提醒，并可关联目标；这里新建的提醒归属主对话。同时仅允许一个 active/paused Heartbeat，已取消或完成的历史任务不占名额。

动态消息右上菜单提供关注说明与来源管理，保留 [OpenAI 官方动态](https://openai.com/news/rss.xml)、[Python 官方博客](https://blog.python.org/feeds/posts/default?alt=rss) 和 [NASA 官方新闻](https://www.nasa.gov/news-release/feed/)，也可预览后确认添加公开 HTTPS RSS/Atom，随时暂停或恢复。每日当地 06:00 自动排入关注内容和构想作业，优先处理用户聊天。启动或检测到运行间断后，等待连续清醒六十秒再处理自动内容；网络或休眠中断最多延迟恢复两次，分别等待十、三十分钟，沿用同一作业 ID。只补最近一个已到的批次，成功批次不重跑，失败保留上一批。生成时间按实际完成记录。

关注内容为零至五条，通常每条两段、140 至 300 字，资料不足时允许更短。先读取订阅中的正文，必要时只补读订阅已提供的文章链接，记录实际读取范围。来源配图通过本机校验代理读取，图片失败保留图标、正文与来源。列表每次加载二十条，支持继续浏览及失败重试。点击“讨论”保持右侧动态，在左侧现有聊天挂上可移除的引用，保留草稿，不自动发送；窄屏在动态和聊天之间切换。构想成功后一次替换为五条，详情的“现在就开始”立即新建副对话、自动发送并启动工作。

任务页的本机关注事项也会进入简报，其来源链接仅用于标注。Heartbeat 使用当前工作流筛选本机事项与订阅，结果为空或没有变化时保持安静；来源读取失败会形成错误记录。使用“优先事项”工作流才能将没有截止时间的 RSS 纳入提醒，“临近截止”工作流仅选有截止日期的事项。构想由真实模型结合有效记忆、目标与关注事项生成，成果库仅提供查看和下载，聊天仍可准备文档提案。生成内容基于实际读取资料，重要事实仍应打开原始来源核对。

普通站内提醒默认自动投递到创建时的对话。副对话封存期间转主对话并标明来源，解封后未来触发回原话题，旧消息不搬动。文档导出始终等待批准，批准前显示完整内容、目标和版本，改动后重新审批。原回复与头像审核请求索引共用同一动作；无原对话的后台提案只在审核请求中显示。严格通知策略会对每条提醒逐条审批，课程演示应使用它。当前外部执行能力限于本项目 `data/exports` 中的文档；影音制作、跨网站交易和云端虚拟机在约定范围之外。

## 验证与演示

| 入口 | 用途 |
|---|---|
| `./test` | 离线测试，不调用付费 API |
| `./test --live` | 真实 DeepSeek 验收，使用隔离固定样例，消耗模型额度 |
| `./test --live --refinement` | 产品真实闭环验收，保留原有组并覆盖 L01 至 L04、自动恢复、目标生命周期、全文搜索、自定义来源和图文讨论 |
| `./navi --help` | 查看 CLI Adapter 命令 |
| `./navi sessions` | 查看与 Web 相同的会话 |
| `./navi search-sessions '标题'` | 完整标题搜索，包含封存话题 |
| `./navi rename-session <id> '新标题'` | 重命名副对话 |
| `./navi archive-session <id>` / `unarchive-session` | 明确封存或解封 |
| `./navi deletion-impact <id>` / `delete-session` | 查看影响或永久删除，并取消关联提醒 |
| `./navi chat '检查关注事项' --wait` | 真实对话与等待结果 |
| `./navi schedule '简报' '检查关注事项' --kind recurring --interval 3600 --in 60` | 创建周期调度 |
| `./navi schedules` | 查看调度 ID 与状态 |
| `./navi pause <schedule-id>` / `resume` / `cancel-schedule` | 管理生命周期 |
| `./navi edit-schedule <id> --interval 7200 --instruction '新指令'` | 修改未来触发 |
| `./navi actions` | 查看具体提案与内容哈希 |
| `./navi approve <action-id> --hash <content-hash>` | 批准所查看的内容 |
| `./navi reject <action-id> --hash <content-hash>` | 拒绝，零投递 |
| `./navi evidence <job-id>` | 导出关联作业证据 |

默认单请求 60 秒、作业执行 180 秒、每作业 5 次模型请求与 10 次工具调用、每次输出 4096 tokens。本机每日请求与 token 限制现为 0，表示不按日总量拦截，实际用量继续记账；服务商额度仍由服务商控制。自动内容作业跨延迟恢复累计计算五次请求、十次工具和 60000 tokens，人工审批等待不占执行期限。用量包含缓存输入；费用粗估不区分缓存优惠，以服务商账单为准。失败且缺少完整 usage 回执的请求保守记账。

课程要求与证据对应见 [验收说明](engineering/evidence/01-initial/ACCEPTANCE.md)，模块边界见 [架构说明](engineering/design/ARCHITECTURE.md)，视觉检查见 [Muse 对齐记录](engineering/design/MUSE_ALIGNMENT.md)。配置字段示例见 `docs/config.example.json`，仅作参考，实际设置在网页保存。真实记录在 `engineering/evidence/04-muse-revision-v1/implementation/live-evidence.json`，启动恢复记录在 `engineering/evidence/01-initial/runtime-evidence.json`，界面操作记录在 `engineering/evidence/01-initial/ui-evidence.json`。`engineering/verification/state/live-*` 保存隔离验收状态，不属于日常个人数据。

本轮细化开发记录见 [产品细化验收](engineering/evidence/03-refinement/ACCEPTANCE.md)。本轮界面和启动检查使用独立 `engineering/verification/.cache/refinement-preview` 数据，测试内容不进入日常数据。日常数据库首次迁移前自动保存 `data/navi.before-refinement.sqlite3`，旧会话保留为副对话，旧来源缺失的条目明确显示暂无关联对话。

2026-10-07 的 `./test --live` 与 `./test --live --refinement` 均使用真实 DeepSeek 通过，覆盖基础八组、原有细化七组及 Muse 新增五组闭环，见 [真实验收汇总](engineering/evidence/04-muse-revision-v1/implementation/live-completion.json) 和 [Muse 修改实施记录](engineering/evidence/04-muse-revision-v1/implementation/ACCEPTANCE.md)。测试使用隔离数据，模型请求计入现有预算。[2026-10-04 的此前模型闭环证据](engineering/evidence/04-muse-revision-v1/implementation/refinement-before-revision.json) 继续保留。一周真实使用仍待完成，记录见 [试用表](engineering/evidence/03-refinement/WEEK_TRIAL.md)。

当前 v2 的五阶段实现及 2026-10-09 验证以 [v2 实施验收](engineering/evidence/05-muse-revision-v2/implementation/ACCEPTANCE.md) 为准。已观察次日实际自动生成，持续清醒的准点 06:00、设备真实休眠恢复及一周试用仍待自然观察。开发日志等待用户审核批准后维护。

版本覆盖原始 Basic 70 与 Advanced 30 的全部工程项目。具体评分由课程评审决定，日常自动提醒与原始逐条审批要求的差异已单独说明。
