# Navi Muse 修改实施与验收记录

2026-10-07，用户以“依照 Navi Muse Revision Plan 执行”授权实施。R01 至 R10 的代码与三阶段迁移已完成，基础、原有细化及 Muse 新增共 20 组即时真实验收全部通过。此前真实验收因每日 100 次请求上限中断；用户随后明确要求继续全部真实验收，并使用每日 300 次请求、300000 tokens，现已持久设置并保留。汇总见 live-completion.json。此记录更新历史结论，原始 PDF、表格和批准计划保留。

本轮覆盖旧 Product Refinement Plan 的局部选择：搜索改为覆盖弹窗，首轮自动主题名及独立摘要，动态与构想自动生成，构想弹窗点击即启动，成果库只查看下载，完成目标结束真实关联工作，分类立即开启持久引导。手动和来源不明的旧名称保留。未提交、推送或发布。

| 项目 | 已实现行为 | 对应证据 |
| --- | --- | --- |
| R01 | 头像在自身聊天面板中心，去除重复顶栏标题、摘要和导航字标 | stage1-chat-title.jpg；桌面并排偏差 0.5px |
| R02 | 首轮成功后 session_meta 独立后台任务，标题 16 字内、摘要 60 字内；提交复核手动名 | live-stage2.json；test_muse_revision.py；长手动名称省略截图 |
| R03 | 动态右上说明与三来源管理；06:00 自动整理零至五条，链接与发布时间取实际输入 | stage2-sources.jpg；live-stage2.json；live-stage3.json 真实自动调度与发布通过 |
| R04 | 结构化单条构想，有具体标题、摘要、包含项目、运作方式与获准动作 | stage2-ideas.jpg；stage2-idea.jpg |
| R05 | 完整五条事务替换，失败保留上批，历史 ID 可读取，首页不显示时间与引用按钮 | 原子发布、取消竞争及 100 批次测试；真实自动生成报告 |
| R06 | 详情弹窗一个开始按钮，幂等建立副对话并自动发送，不需再次发送 | live-stage2.json；实际首份输出、标题与摘要、零未批准导出断言 |
| R07 | 近期二十条与标题字面分页，防抖、键盘操作、焦点与草稿/阅读位置/详情保持 | stage1-search.jpg；stage1-320-search.jpg；1000 话题测试；visual-checks.json |
| R08 | 成果库及详情只查看下载，聊天导出审批保留 | stage1-artifacts.jpg；浏览器实际下载 Navi-draft.md；原有真实导出闭环 |
| R09 | 紧凑勾选行、隐藏空区块，完成取消精确结束关联作业、审批及 Outbox；恢复仅目标 | stage3-completed.jpg；生命周期/迟到结果测试；个人数据一致核对 |
| R10 | 分类新会话自动提问，持久选项与版本校验，最多两次答复确认；确定性保存后再补细节 | stage3-paired-goal.jpg；stage3-choice-selected.jpg；选项重复/过期/刷新/确认测试；live-stage3.json 兴趣与财务完整真实目标闭环通过 |

界面使用隔离预览数据库。关注内容和构想文字来自阶段二真实报告；兴趣选项来自已经完成的 DeepSeek 请求并重放，回放不作为新的真实 API 证据，来源详见 ui-replay.json。预览没有密钥，实际点击选项后的“待配置”失败及持久选择均如实截图。个人对话全文和密钥未复制到证据。

桌面 1280×802：除导航外聊天比例 39.9995%，头像中心偏差 0.5px，无横向溢出。900×700 使用聊天/目标切换；375×812 的聊天、草稿和卡片刷新恢复；320×812 搜索宽 288px，无横向溢出。实际 Chrome 原生缩放控件显示 200%，完成目标、搜索及聊天检查，之后恢复 100% 并关闭临时标签。截图仅保存测试页面，隐藏其他 Chrome 标签和账号栏。入口、草稿、详情和焦点检查见 visual-checks.json。

离线最终检查：./test 共 76 项通过；npm --prefix frontend run build、Python compileall、git diff --check 通过。保留一个原有 Starlette/httpx 弃用提示。新测试覆盖 1000 个标题、100 个历史批次、备份不覆盖、06:00 前后及跨日/错过日期/重启、字面标题检索、整批失败、取消迟到响应、目标版本/保存幂等/精确终止/否定和重名澄清。固定离线模型用于状态与事务检查，不宣称其文字质量等同真实模型。

三个阶段 start.command、stop.command、status.command 实测记录为 runtime-stage1.json、runtime-stage2.json、runtime-stage3.json：退出启动器后 PID 1 托管、无终端，重复启动不新增进程，停止与重新启动保留持久状态，CLI/Web 会话一致。关闭测试浏览器后实际静默调度成功，零模型请求、零动作，见 browser-closed-background.json。尚未实际等到次日 06:00；日期触发由可控离线时钟验证，真实 scheduler 启用时补最近一次并使用真实 API 生成，二者证据分开。

真实 API 检查沿用 ./test --live --refinement，追加 muse_revision_live.py，保留旧七组验收。最终完整执行通过：原有七组包含保存、修改、提醒、封存转发、解封、导出审批及删除后重建；Muse 新增五组包含 scheduler 自动内容、完整五条构想、立即启动与后台主题摘要、兴趣和财务各两轮确认后的保存/提醒/完成/恢复/自然语言取消，以及直接“确认目标：…”保存。兴趣和财务确认均为两次用户答复，双击保存只有一条记录，实际提醒绑定真实目标 ID，结束后旧提醒不复活。对应 engineering/evidence/04-muse-revision-v1/implementation/refinement-live-evidence.json 与本目录 live-stage3.json 均为 passed。

随后 ./test --live 基础八组也全部通过，见 engineering/evidence/04-muse-revision-v1/implementation/live-evidence.json：真实工具回传、Heartbeat 自动触发与严格拒绝、Outbox 幂等投递、两个 Skill 的实际切换、真实长会话压缩并保留目标与待审 ID、审批后文件导出、日常自动提醒、Python 官方 RSS 和重建后的持久状态。本次续验没有新增代码修复。阶段三此前暴露的构想格式、“已保存目标”授权识别及“不设置提醒”否定问题已修复；历史失败及两次预算中断报告继续保留，包含 refinement-request-budget-failure.json 和 live-stage3-request-budget-failure.json。

此前用户明确批准暂时提高每日 token 上限至 200000，验收之后已恢复 100000。最新授权为“继续所有的真实验收 使用限额为每天300次请求300000tokens”，每日限额已持久设置为 300 次、300000 tokens，并按本次要求保留，见 budget-authorization.json。继续前当日预算记账为 100 次、157970 tokens；最终核对为 164 次、265398 tokens，含一条保守预留记录，增量也包含个人应用在此期间的正常使用。CLI 与实际 Web 均已确认新限额生效。费用价格未配置，不给估价。

变更前工作区快照：data/backups/before-muse-revision-2026-10-07.tar.gz。真实数据库逐步升级至 schema 4，备份为 data/navi.before-muse-revision-v3.sqlite3 和 v4；旧备份不覆盖。迁移前已有个人目标、记忆、成果及原有任务和提醒逐行一致，见 migration-personal-preservation.json。个人目录在续验期间另有一条 goal_save 新增记录，来源作业与隔离验收作业 ID 无交集，该正常应用记录保留。模型用量及验收审计会增加。原来个人失败提醒未替用户修复。

个人后台继续运行，使用原 data，实际网页已打开，CLI/Web 共享同一状态；运行记录见 personal-runtime.json。即时真实验收全部通过，一周真实试用和实际跨到次日 06:00 的观察尚未完成。预算以外的云端 VM、任意网站/邮箱/日历、完整影音及交易均未纳入本轮。
