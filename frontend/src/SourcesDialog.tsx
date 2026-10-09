import { useRef, useState } from 'react'
import { api, time, type State } from './data'
import { Dialog } from './Dialog'
import s from './App.module.css'

type Preview = { url: string; title: string; domain: string; items: { title: string; source_url: string; published_at: string }[] }

export function SourcesDialog({ state, trigger, close, refresh }: { state: State; trigger: HTMLElement | null; close: () => void; refresh: () => Promise<void> }) {
  const [url, setUrl] = useState('')
  const [title, setTitle] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const running = useRef(false)
  const generation = useRef(0)

  async function run(key: string, action: () => Promise<void>) {
    if (running.current) return
    running.current = true; setBusy(key); setError(''); setNotice('')
    try { await action() } catch (error) { setError((error as Error).message) }
    finally { running.current = false; setBusy('') }
  }
  function changeUrl(value: string) {
    generation.current++; setUrl(value); setPreview(null); setError(''); setNotice('')
  }
  async function check() {
    const version = generation.current
    await run('preview', async () => {
      const value = await api<Preview>('/feed-sources/preview', 'POST', { url })
      if (version === generation.current) setPreview(value)
    })
  }
  async function confirm() {
    if (!preview) return
    await run('add', async () => {
      await api('/feed-sources', 'POST', { url: preview.url, title })
      await refresh(); generation.current++; setPreview(null); setUrl(''); setTitle(''); setNotice('来源已保存，将用于之后的自动更新。')
    })
  }
  return <Dialog title="管理来源" trigger={trigger} close={close}>
    <form onSubmit={event => { event.preventDefault(); void check() }} className={s.sourceForm}>
      <label>公开 RSS / Atom 订阅地址<input data-first type="url" value={url} maxLength={2048} placeholder="https://…" disabled={busy === 'add'} onChange={event => changeUrl(event.target.value)} required /></label>
      <label>名称，可选<input value={title} maxLength={80} disabled={busy === 'add'} onChange={event => setTitle(event.target.value)} /></label>
      <button className={s.secondary} disabled={!!busy || !url.trim()} type="submit">{busy === 'preview' ? '正在读取…' : '预览来源'}</button>
      {preview && <div className={s.sourcePreview}>
        <strong>{preview.title}</strong><p className={s.subtle}>{preview.domain}</p>
        <ul>{preview.items.map(item => <li key={item.source_url}><a href={item.source_url} target="_blank" rel="noreferrer">{item.title}</a><span>{new Date(item.published_at).toLocaleDateString('zh-CN')}</span></li>)}</ul>
        <button type="button" className={s.primary} disabled={!!busy} onClick={() => void confirm()}>{busy === 'add' ? '正在确认…' : '确认添加'}</button>
      </div>}
      {error && <p role="alert" className={s.failureText}>{error}</p>}
      {notice && <p role="status" className={s.subtle}>{notice}</p>}
    </form>
    {state.feed_catalog.map(source => {
      const row = state.records.find(row => row.id === 'source-' + source.id)
      return <div className={s.sourceRow} key={source.id}><div><strong>{source.title}</strong><p>{row?.payload.enabled === false ? '已暂停' : row?.payload.last_error ? String(row.payload.last_error) : row?.payload.last_success ? '最近成功 ' + time(Number(row.payload.last_success)) : '尚未首次检查'}</p><a href={source.url} target="_blank" rel="noreferrer">订阅地址</a></div><button className={s.secondary} disabled={!!busy} onClick={() => void run(source.id, async () => { await api(row ? '/feed-sources/' + row.id + '/toggle' : '/feed-sources', 'POST', row ? {} : { source_id: source.id }); await refresh() })}>{!row ? '关注' : row.payload.enabled === false ? '恢复' : '暂停'}</button></div>
    })}
    <p className={s.subtle}>预览只读取公开订阅内容。确认后保存，新增来源用于之后的自动更新。</p>
  </Dialog>
}
