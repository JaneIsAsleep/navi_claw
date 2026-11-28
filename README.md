# Navi

本机个人 AI 助理。聊天、记忆、目标、动态、建议与成果共用一个持久 Gateway。CLI 和 Web 操作相同状态。

项目根目录为 `navi_claw` 文件夹。

## 开始使用

macOS/Linux 用户双击或执行 `start.command` 启动，Windows 用户双击 `start-windows.cmd` 启动。启动入口会自动检查并准备缺失的 uv、Python 依赖、Node.js 和前端产物；依赖已完整时直接复用现有环境。就绪后打开 [Navi](http://127.0.0.1:8766)，重复启动复用已有后台。

启动完成后，启动终端窗口可以关闭。关闭浏览器也不会停止任务。macOS/Linux 使用 `stop.command` 和 `status.command`；Windows 使用 `stop-windows.cmd` 和 `status-windows.cmd`。

使用 `./run` 会在前台运行，需要保留终端，按 `Ctrl+C` 正常停止。请先停止后台再使用前台入口。

在项目目录执行 `./start.command --no-browser`，再运行 `./navi` 或 `./navi tui`，即可打开全屏终端界面；原生 Windows 使用 `navi-windows.cmd` 或 `navi-windows.cmd tui`。与 Web 共用会话、搜索设置、提醒和审批。已有环境可用 `uv sync --locked --python 3.12` 安装新增依赖。

`F1` 打开命令面板，`F2` 切换侧栏，`Ctrl+N` 新建对话，`Ctrl+Enter` 或 `F8` 发送，`Ctrl+Q` 退出界面。用 `Tab` 切换控件，方向键浏览列表，`Enter` 选择。窄终端默认收起侧栏。

侧栏可以搜索会话标题和正文，并查看校园、目标、提醒、动态、构想、成果、记忆、审批和活动。校园页展示模型整理后的近期消息与临近 DDL。可以在终端批准或拒绝提案、开始构想、引用动态、切换对话联网和设置原生搜索。目标与提醒也可以在对话中提出。下载成果、编辑订阅来源和配置自定义模型端点可在 Web 完成。

重启保留会话、记忆、目标、调度、作业、审批、消息和成果。电脑休眠或停止后台期间不执行检查，恢复后每个调度最多补做一次最近错过的检查，不补跑全部周期。

首次准备需要网络。Linux/macOS 会自动安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)；缺少 Node.js 时，Linux 会下载项目内的 22.12 版本，macOS 需要预先安装 Node.js 22.12 或更高版本。Windows 会把运行环境和 Node.js 放在项目目录内，不需要管理员权限。Python 与前端分别由 `uv.lock` 和 `frontend/package-lock.json` 锁定依赖。也可手动执行 `./setup` 或 `setup-windows.cmd` 重新检查环境。

Windows 也可继续使用 WSL2 的 Ubuntu 运行原入口。在管理员 PowerShell 中执行以下命令，按提示重启并完成 Ubuntu 首次配置，见 [Microsoft 安装说明](https://learn.microsoft.com/en-us/windows/wsl/install)。

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

在 Windows 浏览器打开 [Navi](http://localhost:8766)，首次使用在设置页填写自己的 DeepSeek 密钥。[Microsoft 的 WSL 网络说明](https://learn.microsoft.com/en-us/windows/wsl/networking) 支持从 Windows 浏览器通过 localhost 访问 Linux 服务。WSL2 作为兼容方式保留；WSL 退出、Windows 休眠或关机期间无法执行后台任务。无需双击 `.command` 文件，在 Ubuntu 终端执行 `./stop.command`、`./status.command` 即可。

仓库共享源码、锁定依赖、工程资料与验证代码。个人密钥、数据库、聊天状态和原始私人文本证据留在忽略版本控制的 `data/` 中；截图材料按用户确认共享。`engineering/requirements/修改队列.xlsx` 含敏感截图，只保存在维护者本机，克隆仓库不会获得该文件；对应需求见计划与已共享证据。每位组员独立创建本机状态，完整备份仅用于本机恢复。历史日志中的旧提交号保存在维护者本机归档分支，小组仓库从脱敏后的开发基线开始；旧阶段的验收材料仍随仓库保留。开发时从最新 `main` 创建功能分支，通过 Pull Request 汇合；提交前执行 `./test` 和 `npm --prefix frontend run build`。`./test --live` 会使用当前机器的密钥并消耗模型额度。

更新前先保存自己分支的工作。回到 `main` 后先停止当前平台的后台入口，执行 `git pull --ff-only`，再通过对应平台的启动入口自动检查环境；macOS/Linux 也可执行 `./setup`、`./test`、`./start.command`。

在[本机设置页](http://127.0.0.1:8766/#settings)填写 DeepSeek 密钥，无需发到聊天里。本次真实验证使用 `deepseek-flash` 与 Messages 协议。模型名称可修改，只有实际连接检查成功才代表你的账户可用。接口参考 [DeepSeek 官方英文实现](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm-deepseek/README.md)。

对话和后台生成时，Navi 会把当前会话、摘要、有效记忆、进行中的目标、待办上下文及本次来源资料发送给你配置的 DeepSeek 服务。停用的记忆不参与后续回答。数据文件保存在本机；密钥位于忽略版本控制的 `data/.credentials.json`，权限为 `0600`，不会进入普通日志或证据。

首次进入唯一主对话，以后恢复最近的有效话题和草稿。主对话不可重命名、封存或删除。副对话在发出首条消息后进入历史，可从行内更多菜单管理；Web 搜索匹配标题和已保存消息正文，显示命中片段，包含封存话题。搜索及查看命中无需模型，打开历史不会自动解封。CLI 默认继续按标题检索。首轮成功后异步生成简短主题名，手动名称保留。永久删除会取消该话题的未完成提醒与未决动作，保留独立保存的目标、记忆、成果及必要执行记录。

可以从“记住：请用简洁中文回答我”开始。头像 → 身份信息 → 记忆提供逐条管理。目标分类先展示说明，点击“现在就开始”才创建话题并开始选项引导，关闭说明不写入数据。选项位于聊天气泡内，最多两次答复后展示可确认草案。确认保存由 Gateway 持久化，之后再补行动步骤或提醒。桌面聊天和目标可以并排或收起，窄屏切换保留草稿。勾选完成时立即结束关联工作，完成行约三秒后移入已完成列表；已完成列表支持恢复及移除目标，保留相关对话、文件和审计。恢复目标不会恢复旧提醒。头像 → 提醒事项设置单次、周期或 Heartbeat 提醒，并可关联目标；这里新建的提醒归属主对话。同时仅允许一个 active/paused Heartbeat，已取消或完成的历史任务不占名额。

动态消息右上菜单提供关注说明与来源管理，保留 [OpenAI 官方动态](https://openai.com/news/rss.xml)、[Python 官方博客](https://blog.python.org/feeds/posts/default?alt=rss) 和 [NASA 官方新闻](https://www.nasa.gov/news-release/feed/)，也可预览后确认添加公开 HTTPS RSS/Atom，随时暂停或恢复。每日当地 06:00 自动排入关注内容和构想作业，优先处理用户聊天。启动或检测到运行间断后，等待连续清醒六十秒再处理自动内容；网络或休眠中断最多延迟恢复两次，分别等待十、三十分钟，沿用同一作业 ID。只补最近一个已到的批次，成功批次不重跑，失败保留上一批。生成时间按实际完成记录。

校园页可使用南科大 CAS 连接校园平台。点击“使用独立浏览器登录”后，Navi 打开唯一一个独立 Edge 窗口并进入真实 CAS 页面，在窗口中完成账号密码和滑块验证。TIS 与 Blackboard 的后续跳转都在同一个窗口中完成，不创建额外窗口。登录成功后窗口最小化并保留本次校园会话；退出 CAS 或停止 Navi 时关闭。系统 Edge 与 Navi 使用相互隔离的浏览器会话，因此系统 Edge 已登录不会自动共享给 Navi。密码只用于本次 CAS 登录，不写入数据库或会话文件；本机仅保存加密后的平台会话。Blackboard 看板只保留近期消息和临近 DDL，作业与考试统一归入 DDL，并交给当前模型生成总览和任务摘要；已过期或重复事项会被过滤，页面只在存在已验证的 Blackboard 地址时提供外部入口。看板缓存一小时；缓存过期后先继续显示旧内容并在后台更新，校园页可见时每五分钟检查一次缓存，手动刷新会立即重新读取并整理。聊天中使用校园内容时，相关结构化信息会发送给当前模型服务；Cookie 和密码不会发送给模型。会话失效时需要重新登录。

校园接口、登录状态机、缓存策略和分支合并适配清单见 [校园平台集成说明](docs/CAMPUS_INTEGRATION.md)。

关注内容为零至五条，通常每条两段、140 至 300 字，资料不足时允许更短。先读取订阅中的正文，必要时只补读订阅已提供的文章链接，记录实际读取范围。来源配图通过本机校验代理读取，图片失败保留图标、正文与来源。列表每次加载二十条，支持继续浏览及失败重试。点击“讨论”保持右侧动态，在左侧现有聊天挂上可移除的引用，保留草稿，不自动发送；窄屏在动态和聊天之间切换。构想成功后一次替换为五条，详情的“现在就开始”立即新建副对话、自动发送并启动工作。

任务页的本机关注事项也会进入简报，其来源链接仅用于标注。Heartbeat 使用当前工作流筛选本机事项与订阅，结果为空或没有变化时保持安静；来源读取失败会形成错误记录。使用“优先事项”工作流才能将没有截止时间的 RSS 纳入提醒，“临近截止”工作流仅选有截止日期的事项。构想由真实模型结合有效记忆、目标与关注事项生成，成果库仅提供查看和下载，聊天仍可准备文档提案。生成内容基于实际读取资料，重要事实仍应打开原始来源核对。

普通站内提醒默认自动投递到创建时的对话。副对话封存期间转主对话并标明来源，解封后未来触发回原话题，旧消息不搬动。文档导出始终等待批准，批准前显示完整内容、目标和版本，改动后重新审批。原回复与头像审核请求索引共用同一动作；无原对话的后台提案只在审核请求中显示。严格通知策略会对每条提醒逐条审批，课程演示应使用它。当前外部执行能力限于本项目 `data/exports` 中的文档；影音制作、跨网站交易和云端虚拟机在约定范围之外。

## 验证与演示

| 入口 | 用途 |
|---|---|
| `start-windows.cmd` | Windows 原生启动，首次自动准备环境 |
| `stop-windows.cmd` | Windows 原生停止本项目后台 |
| `status-windows.cmd` | Windows 原生查看状态、入口和日志 |
| `setup-windows.cmd` | Windows 手动检查并修复运行环境 |
| `./test` | 离线测试，不调用付费 API |
| `./test --live` | 真实 DeepSeek 验收，使用隔离固定样例，消耗模型额度 |
| `./test --live --refinement` | 产品真实闭环验收，保留原有组并覆盖 L01 至 L04、自动恢复、目标生命周期、全文搜索、自定义来源和图文讨论 |
| `./navi --help` | 查看 CLI Adapter 命令 |
| `./navi sessions` | 查看与 Web 相同的会话 |
| `./navi campus [--refresh]` | 查看 Blackboard 近期消息与 DDL，可强制刷新模型摘要 |
| `navi-windows.cmd campus [--refresh]` | Windows 原生查看校园看板；无参数运行 `tui` |
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

CLI 与 Web 共用每个未封存对话的联网搜索开关。不指定 `--session` 或 `--new-session` 时使用主对话。`chat` 省略开关参数时沿用会话已保存的设置。示例中的 `SESSION_ID` 取自第一条命令返回的 `session_id`。

```sh
./navi chat '联网搜索 Python 官方发布消息并附上来源' --new-session --web-search --wait
./navi web-search status --session "$SESSION_ID"
./navi web-search off --session "$SESSION_ID"
./navi chat '整理刚才查到的信息' --session "$SESSION_ID" --no-web-search --wait
```

`web-search on` 可以预先开启指定对话。`./navi search-settings` 查看当前第三方服务、密钥配置状态和原生搜索测试结果。`./navi search-settings --provider brave --set-key` 选择服务并通过隐藏输入保存密钥，可选服务为 `bocha`、`brave`、`anysearch`。`./navi check-native-search --wait` 用当前 DeepSeek 连接发起真实测试，会消耗模型额度。联网搜索优先使用通过验证的 DeepSeek 原生搜索，未验证或失败时尝试已配置的第三方服务。

默认单请求 60 秒、作业执行 180 秒、每作业 5 次模型请求与 10 次工具调用、每次输出 4096 tokens。本机每日请求与 token 限制现为 0，表示不按日总量拦截，实际用量继续记账；服务商额度仍由服务商控制。自动内容作业跨延迟恢复累计计算五次请求、十次工具和 60000 tokens，人工审批等待不占执行期限。用量包含缓存输入；费用粗估不区分缓存优惠，以服务商账单为准。失败且缺少完整 usage 回执的请求保守记账。

课程要求与证据对应见 [验收说明](engineering/evidence/01-initial/ACCEPTANCE.md)，模块边界见 [架构说明](engineering/design/ARCHITECTURE.md)，视觉检查见 [Muse 对齐记录](engineering/design/MUSE_ALIGNMENT.md)。配置字段示例见 `docs/config.example.json`，实际设置可在网页保存。真实记录在 `engineering/evidence/04-muse-revision-v1/implementation/live-evidence.json`，启动恢复记录在 `engineering/evidence/01-initial/runtime-evidence.json`，界面操作记录在 `engineering/evidence/01-initial/ui-evidence.json`。`engineering/verification/state/live-*` 保存隔离验收状态，不属于日常个人数据。

本轮细化开发记录见 [产品细化验收](engineering/evidence/03-refinement/ACCEPTANCE.md)。本轮界面和启动检查使用独立 `engineering/verification/.cache/refinement-preview` 数据，测试内容不进入日常数据。日常数据库首次迁移前自动保存 `data/navi.before-refinement.sqlite3`，旧会话保留为副对话，旧来源缺失的条目明确显示暂无关联对话。

2026-10-07 的 `./test --live` 与 `./test --live --refinement` 均使用真实 DeepSeek 通过，覆盖基础八组、原有细化七组及 Muse 新增五组闭环，见 [真实验收汇总](engineering/evidence/04-muse-revision-v1/implementation/live-completion.json) 和 [Muse 修改实施记录](engineering/evidence/04-muse-revision-v1/implementation/ACCEPTANCE.md)。测试使用隔离数据，模型请求计入现有预算。[2026-10-04 的此前模型闭环证据](engineering/evidence/04-muse-revision-v1/implementation/refinement-before-revision.json) 继续保留。一周真实使用仍待完成，记录见 [试用表](engineering/evidence/03-refinement/WEEK_TRIAL.md)。

当前 v2 的五阶段实现及 2026-10-09 验证以 [v2 实施验收](engineering/evidence/05-muse-revision-v2/implementation/ACCEPTANCE.md) 为准。已观察次日实际自动生成，持续清醒的准点 06:00、设备真实休眠恢复及一周试用仍待自然观察。实施经过见 [开发日志](engineering/logs/DEVELOPMENT_LOG.md)。

版本覆盖原始 Basic 70 与 Advanced 30 的全部工程项目。具体评分由课程评审决定，日常自动提醒与原始逐条审批要求的差异已单独说明。
