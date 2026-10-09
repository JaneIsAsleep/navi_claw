import { Check } from 'lucide-react'
import { type Action, labels, time } from './data'
import { Markdown } from './Markdown'
import s from './App.module.css'

export function ActionCard({ row, working, decide, edit, openSource, history }: { row: Action; working: boolean; decide: (row: Action, decision: string) => void; edit: (row: Action) => void; openSource: (id: string) => void; history: (path: string) => void }) {
  const container = useRef<HTMLElement>(null)
  function decision(value: string) { container.current?.focus(); decide(row, value) }
  return <article ref={container} tabIndex={-1} className={s.actionCard} aria-label={'审核请求 ' + row.title}>
    <div className={s.actionHeading}><h3>{row.title}</h3><span className={s.badge}>{row.delivery_state === 'uncertain' ? labels.uncertain : labels[row.status]}</span></div>
    <p>{row.kind === 'export' ? '写入本机成果文件：' + row.target : row.source_session_id ? '站内提醒，正常发回创建对话；封存期间转发主对话并标明来源。' : '站内提醒，暂无关联对话，保留在提醒历史。'}</p>
    <Markdown>{row.body}</Markdown>
    <div className={s.meta}><span>版本 {row.version} · {time(row.created)}</span><span>{row.basis || '等待你决定'}</span></div>
    {row.delivery_error && <p className={s.failureText}>{row.delivery_error}</p>}
    {row.source_title ? <button className={s.textButton} onClick={() => openSource(row.source_session_id!)}>原提案：{row.source_title}{row.source_state === 'archived' ? '（已封存）' : ''}</button> : <p className={s.subtle}>暂无关联对话</p>}
    <button className={s.textButton} onClick={() => history('/actions/' + row.id + '/history')}>版本与执行记录</button>
    {row.status === 'pending' && <div className={s.approvalButtons}>
      <button className={s.primary} disabled={working} onClick={() => decision('approve')}><Check size={16} />批准执行</button>
      <button className={s.secondary} disabled={working} onClick={() => decision('reject')}>拒绝</button>
      <button className={s.textButton} disabled={working} onClick={() => edit(row)}>编辑后重新审批</button>
    </div>}
  </article>
}
import { useRef } from 'react'
