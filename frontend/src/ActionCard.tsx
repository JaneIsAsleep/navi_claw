import { useRef } from 'react'
import { Check } from 'lucide-react'
import { type Action, labels, time } from './data'
import { Markdown } from './Markdown'
import s from './App.module.css'

type EditValue = string | number | null
type ItemEdit = {
  before: Record<string, EditValue>
  changes: Record<string, EditValue>
  timezone: string
  linked_reminders: { id: string; title: string }[]
}
const fields: Record<string, string> = { title: '标题', body: '内容', instruction: '提醒内容', next_step: '下一步', next_due: '下次提醒时间', state: '状态' }

function editValue(field: string, value: EditValue, timezone: string) {
  if (value == null || value === '') return '未填写'
  if (field === 'next_due' && typeof value === 'number') {
    return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'medium', timeZone: timezone }).format(value * 1000) + `（${timezone}）`
  }
  return field === 'state' ? labels[value] || value : value
}

export function ActionCard({ row, working, decide, edit, openSource, history, dismissReminder }: { row: Action; working: boolean; decide: (row: Action, decision: string) => void; edit: (row: Action) => void; openSource: (id: string) => void; history: (path: string) => void; dismissReminder?: (id: string) => void }) {
  const container = useRef<HTMLElement>(null)
  const itemEdit = row.kind === 'item_edit' ? JSON.parse(row.body) as ItemEdit : null
  const status = itemEdit && row.status === 'delivered' ? '已执行' : itemEdit && row.status === 'pending' ? '待审批' : labels[row.status]
  function decision(value: string) { container.current?.focus(); decide(row, value) }
  return <article ref={container} tabIndex={-1} className={s.actionCard} aria-label={'审核请求 ' + row.title}>
    <div className={s.actionHeading}><h3>{row.title}</h3><span className={s.badge}>{row.delivery_state === 'uncertain' ? labels.uncertain : status}</span></div>
    {itemEdit ? <>
      <p className={s.editTarget}>记录 ID <code>{row.target}</code></p>
      <dl className={s.editChanges}>{Object.entries(itemEdit.changes).map(([field, value]) => <div key={field}>
        <dt>{fields[field]}</dt>
        <dd><span>修改前</span>{editValue(field, itemEdit.before[field], itemEdit.timezone)}</dd>
        <dd><span>修改后</span>{editValue(field, value, itemEdit.timezone)}</dd>
      </div>)}</dl>
      {!!itemEdit.linked_reminders.length && <p>同时结束关联提醒 {itemEdit.linked_reminders.map(reminder => reminder.title).join('、')}</p>}
    </> : <>
      <p>{row.kind === 'export' ? '写入本机成果文件：' + row.target : row.source_session_id ? '站内提醒，正常发回创建对话；封存期间转发主对话并标明来源。' : '站内提醒，暂无关联对话，保留在提醒历史。'}</p>
      <Markdown>{row.body}</Markdown>
    </>}
    <div className={s.meta}><span>版本 {row.version} · {time(row.created)}</span><span>{row.basis || '等待你决定'}</span></div>
    {row.delivery_error && <p className={s.failureText}>{row.delivery_error}</p>}
    {row.source_title ? <button className={s.textButton} onClick={() => openSource(row.source_session_id!)}>原提案：{row.source_title}{row.source_state === 'archived' ? '（已封存）' : ''}</button> : <p className={s.subtle}>暂无关联对话</p>}
    <button className={s.textButton} onClick={() => history('/actions/' + row.id + '/history')}>版本与执行记录</button>
    {dismissReminder && row.kind === 'notification' && (row.status === 'delivered' || row.delivery_state === 'delivered') && row.acknowledged_at == null && (
      <button type="button" className={s.secondary} disabled={working} onClick={() => dismissReminder(row.id)}>收到</button>
    )}
    {row.status === 'pending' && <div className={s.approvalButtons}>
      <button className={s.primary} disabled={working} onClick={() => decision('approve')}><Check size={16} />批准执行</button>
      <button className={s.secondary} disabled={working} onClick={() => decision('reject')}>拒绝</button>
      {!itemEdit && <button className={s.textButton} disabled={working} onClick={() => edit(row)}>编辑后重新审批</button>}
    </div>}
  </article>
}
