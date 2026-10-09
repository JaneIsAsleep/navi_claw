import { useEffect } from 'react'
import { BookOpen, Circle, Pause, Pencil, Play, Plus, Trash2 } from 'lucide-react'
import { type Action, type CheckItem, type Editor, type Item, type State, type Tab, labels, terminal, time } from './data'
import { ActionCard } from './ActionCard'
import { Markdown } from './Markdown'
import s from './App.module.css'

type Props = {
  state: State; tab: Tab; memory: boolean; memoryId: string; setTab: (tab: Tab) => void; setMemory: (value: boolean) => void; working: boolean
  edit: (kind: Editor['kind'], row?: Item | State['schedules'][number] | Action | CheckItem) => void
  act: (path: string, method: string, data: unknown, message?: string) => void
  remove: (row: Item | CheckItem) => void; openSession: (id: string) => void; openRecord: (row: Item) => void
  decide: (row: Action, decision: string) => void; history: (path: string) => void
}
const tabs: { key: Tab; label: string }[] = [{ key: 'activity', label: '动态' }, { key: 'approvals', label: '审核请求' }, { key: 'reminders', label: '提醒事项' }, { key: 'identity', label: '身份信息' }]

export function AssistantDetails(p: Props) {
  const { state, tab } = p
  useEffect(() => {
    if (p.memory && p.memoryId) {
      const entry = document.getElementById('memory-' + p.memoryId)
      const frame = requestAnimationFrame(() => { entry?.scrollIntoView({ block: 'center' }); entry?.focus() })
      return () => cancelAnimationFrame(frame)
    }
  }, [p.memory, p.memoryId])
  return <>
    <div className={s.tabs} role="tablist" aria-label="关于 Navi">
      {tabs.map((row, index) => <button key={row.key} id={'tab-' + row.key} role="tab" aria-selected={tab === row.key} aria-controls="assistant-tabpanel" tabIndex={tab === row.key ? 0 : -1} onClick={() => { p.setTab(row.key); p.setMemory(false) }} onKeyDown={event => {
        if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
          event.preventDefault()
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? 3 : (index + (event.key === 'ArrowRight' ? 1 : 3)) % 4
          p.setTab(tabs[next].key); p.setMemory(false); document.getElementById('tab-' + tabs[next].key)?.focus()
        }
      }}>{row.label}{row.key === 'approvals' && state.actions.some(a => a.status === 'pending') && <span className={s.unreadDot} />}</button>)}
    </div>
    <div id="assistant-tabpanel" role="tabpanel" aria-labelledby={'tab-' + tab} className={s.panelContent}>
      {tab === 'activity' && <><p className={s.subtle}>跟进记录</p>{state.jobs.filter(row => row.kind !== 'compact' && row.kind !== 'connection' && (row.kind !== 'chat' || row.status !== 'succeeded')).map(row => <article className={s.row} key={row.id}><div className={s.rowMain}><h3>{row.input.slice(0, 100)}</h3><div className={s.meta}>{labels[row.status]} · {time(row.created)}</div>{row.error && <p className={s.failureText}>{row.error}</p>}<div className={s.rowActions}>{row.session_id && <button className={s.textButton} onClick={() => p.openSession(row.session_id!)}>回相关对话</button>}{state.records.filter(r => r.payload.job_id === row.id && r.kind === 'artifact').map(r => <button className={s.textButton} key={r.id} onClick={() => p.openRecord(r)}>查看成果</button>)}{!terminal(row.status) && <button className={s.textButton} onClick={() => p.act('/jobs/' + row.id + '/cancel', 'POST', {})}>停止</button>}<button className={s.textButton} onClick={() => p.history('/jobs/' + row.id + '/evidence')}>详细记录</button></div></div></article>)}{!state.jobs.length && <p>有真实进展时，会在这里留下记录。</p>}</>}
      {tab === 'approvals' && <><p className={s.subtle}>先看完整行动与执行目标。批准和实际执行分别记录。</p>{state.actions.map(row => <ActionCard key={row.id} row={row} working={p.working} decide={p.decide} edit={row => p.edit('action', row)} openSource={p.openSession} history={p.history} />)}{!state.actions.length && <p>当前没有需要你决定的动作。</p>}</>}
      {tab === 'reminders' && <>
        <div className={s.sectionTitle}><p>约定何时做什么</p><button className={s.primary} onClick={() => p.edit('task')}><Plus size={16} />设置提醒</button></div>
        <p className={s.subtle}>这里新建的提醒归属主对话。关闭网页后继续运行，电脑休眠或后台停止期间无法执行。</p>
        {state.schedules.map(row => <article className={s.row} key={row.id}><div className={s.rowMain}><h3>{row.title}</h3><p>{row.instruction}</p><div className={s.meta}><span>{labels[row.kind]} · {labels[row.state]}</span><span>{row.enabled ? '下次 ' + time(row.next_due) : '未来触发已停止'}</span></div><p className={s.subtle}>{row.session_id ? '归属：' + (state.sessions.find(s => s.id === row.session_id)?.title || '已封存话题，期间转发主对话') : '暂无关联对话'}</p><div className={s.rowActions}>{row.state !== 'cancelled' && row.state !== 'completed' && <><button className={s.textButton} onClick={() => p.act('/schedules/' + row.id, 'PATCH', { operation: row.enabled ? 'pause' : 'resume' })}>{row.enabled ? <Pause size={16} /> : <Play size={16} />}{row.enabled ? '暂停' : '恢复'}</button><button className={s.textButton} onClick={() => p.edit('task', row)}>修改时间</button><button className={s.textButton} onClick={() => p.act('/schedules/' + row.id, 'PATCH', { operation: 'cancel' }, '已取消，未来不再触发')}>取消提醒</button></>}<button className={s.textButton} onClick={() => p.history('/schedules/' + row.id + '/history')}>历史与失败</button>{row.session_id && <button className={s.textButton} onClick={() => p.openSession(row.session_id!)}>查看归属</button>}</div>{row.kind === 'heartbeat' && <details><summary>自动检查详情</summary><p>Heartbeat 每 {Number(row.interval_seconds) / 60} 分钟检查一次，仅在实际筛选结果变化时提醒。</p></details>}</div></article>)}
        {!state.schedules.length && <p>还没有安排提醒，可以从一句“何时提醒我做什么”开始。</p>}
        <div className={s.sectionTitle}><h3>关注事项</h3><button className={s.textButton} onClick={() => p.edit('check')}><Plus size={16} />添加事项</button></div>
        <p className={s.subtle}>截止日期表示事项期限。保存事项不会自动安排提醒。</p>
        {state.check_items.map(row => <article className={s.row} key={row.id}><button className={s.iconButton} aria-label={(row.done ? '重新打开' : '完成') + row.title} onClick={() => p.act('/check-items', 'PUT', state.check_items.map(item => item.id === row.id ? { ...item, done: !item.done } : item))}><Circle size={18} fill={row.done ? 'currentColor' : 'none'} /></button><div className={s.rowMain}><h3>{row.title}</h3><p>{row.body}</p><div className={s.meta}>{row.due_at ? '截止 ' + time(row.due_at) : '未设截止日期'}{row.done && ' · 已完成'}</div></div><button className={s.iconButton} aria-label={'编辑事项 ' + row.title} onClick={() => p.edit('check', row)}><Pencil size={16} /></button><button className={s.iconButton} aria-label={'删除事项 ' + row.title} onClick={() => p.remove(row)}><Trash2 size={16} /></button></article>)}
        <details className={s.advanced}><summary>历史投递记录</summary>{state.notifications.map(row => <article className={s.row} key={row.id}><div><h3>{row.title}</h3><Markdown>{row.body}</Markdown><span className={s.meta}>{time(row.created)} · 实际投递</span></div></article>)}{!state.notifications.length && <p>暂无投递记录。</p>}</details>
      </>}
      {tab === 'identity' && (p.memory ? <>
        <div className={s.sectionTitle}><button className={s.textButton} onClick={() => p.setMemory(false)}>返回身份信息</button><button className={s.primary} onClick={() => p.edit('memory')}><Plus size={16} />添加记忆</button></div>
        <p className={s.subtle}>启停影响以后的模型调用，过去已经发送的上下文仍存在。</p>
        {state.records.filter(row => row.kind === 'memory').map(row => <article className={s.row} key={row.id} id={'memory-' + row.id} tabIndex={-1}><div className={s.rowMain}><h3>{row.payload.title}</h3><p>{row.payload.body}</p><div className={s.meta}><span>{row.payload.enabled === false ? '已停用，不参与后续回答' : '参与后续回答'}</span><span>{time(row.updated)}</span></div>{row.source_session ? <button className={s.textButton} onClick={() => p.openSession(row.source_session!.id)}>查看真实来源</button> : <p className={s.subtle}>暂无关联对话</p>}<div className={s.rowActions}><button className={s.textButton} onClick={() => p.act('/records/' + row.id, 'PUT', { ...row.payload, enabled: row.payload.enabled === false })}>{row.payload.enabled === false ? <Play size={16} /> : <Pause size={16} />}{row.payload.enabled === false ? '启用' : '停用'}</button><button className={s.textButton} onClick={() => p.edit('memory', row)}>编辑</button><button className={s.textButton} onClick={() => p.remove(row)}><Trash2 size={16} />删除</button></div></div></article>)}{!state.records.some(row => row.kind === 'memory') && <p>还没有记忆。可以手动添加，或在对话中明确说“记住”。</p>}
      </> : <><div className={s.identityMark}>N</div><h3>Navi</h3><p>在你的本机接回交代过的事，按约定跟进，由你决定关键行动。</p><button className={s.secondary} onClick={() => p.setMemory(true)}><BookOpen size={18} />记忆</button><p className={s.subtle}>逐条查看、修改和控制用于后续回答的信息。</p><div className={s.advanced}><h3>运行边界</h3><p>模型调用会发送当前有效上下文给 DeepSeek。普通站内提醒按已有策略投递，文档导出先审批。</p></div></>)}
    </div>
  </>
}
