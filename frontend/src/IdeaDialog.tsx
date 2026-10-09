import { useState } from 'react'
import { api, APIError, type Item } from './data'
import { Dialog } from './Dialog'
import { Markdown } from './Markdown'
import s from './App.module.css'

export function IdeaDialog({ row, trigger, close, started, stale }: { row: Item; trigger: HTMLElement | null; close: () => void; started: (id: string) => void; stale: () => void }) {
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  async function start() {
    if (working) return
    setWorking(true); setError('')
    const key = 'navi-idea-start-' + row.id
    let requestId = sessionStorage.getItem(key) || crypto.randomUUID()
    sessionStorage.setItem(key, requestId)
    try {
      let result: { session_id: string; job_id: string }
      try {
        result = await api('/ideas/' + row.id + '/start', 'POST', { request_id: requestId })
      } catch (failure) {
        if (!(failure instanceof APIError) || failure.status !== 410) throw failure
        requestId = crypto.randomUUID()
        sessionStorage.setItem(key, requestId)
        result = await api('/ideas/' + row.id + '/start', 'POST', { request_id: requestId })
      }
      if (!result.session_id) throw new Error('未取得构想对话，请稍后重试')
      started(result.session_id); close()
    } catch (failure) { setError((failure as Error).message); stale() }
    finally { setWorking(false) }
  }
  return <Dialog title={row.payload.title} trigger={trigger} close={close}>
    {!row.payload.action_kind && <p className={s.subtle}>这是旧版构想，尚无可执行的开始动作。下一批更新成功后会替换为五条可开始的新构想。</p>}
    <h3>我能交付什么</h3><Markdown>{row.payload.body || ''}</Markdown>
    <h3>包含内容</h3>{(row.payload.includes as string[] || []).map(part => <p key={part}>{part}</p>)}
    <h3>如何开始</h3><p>{String(row.payload.how_it_works || '点击后开始处理，缺少资料时会向你确认。')}</p>
    {error && <p role="alert" className={s.failureText}>{error}</p>}
    <button className={s.primary} disabled={working || row.payload.state === 'superseded' || !row.payload.action_kind} onClick={() => void start()}>{working ? '正在开始' : '现在就开始'}</button>
  </Dialog>
}
