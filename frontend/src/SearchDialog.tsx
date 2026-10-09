import { useEffect, useRef, useState } from 'react'
import { Search } from 'lucide-react'
import { api, type Session } from './data'
import { Dialog, type CloseReason } from './Dialog'
import s from './App.module.css'

function excerpt(text: string, query: string) {
  const at = text.toLowerCase().indexOf(query.trim().toLowerCase())
  if (!query.trim() || at < 0) return text
  return <>{text.slice(0, at)}<mark>{text.slice(at, at + query.trim().length)}</mark>{text.slice(at + query.trim().length)}</>
}

export function SearchDialog({ trigger, close, open, recent, cache }: { trigger: HTMLElement | null; close: () => void; open: (row: Session) => Promise<void>; recent: Session[] | null; cache: (rows: Session[]) => void }) {
  const [query, setQuery] = useState('')
  const [rows, setRows] = useState<Session[]>(recent || [])
  const [loading, setLoading] = useState(!recent)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState(0)
  const [more, setMore] = useState(recent?.length === 20)
  const generation = useRef(0)
  const reason = useRef<CloseReason>('keyboard')
  const list = useRef<HTMLDivElement>(null)
  async function read(offset: number, version: number) {
    setLoading(true); setError('')
    try {
      const result = await api<Session[]>('/sessions/search?recent=true&scope=all&limit=20&offset=' + offset + '&q=' + encodeURIComponent(query))
      if (generation.current !== version) return
      setRows(current => offset ? [...current, ...result] : result); setMore(result.length === 20)
      if (!query && !offset) cache(result)
    } catch (failure) { if (generation.current === version) setError((failure as Error).message) }
    finally { if (generation.current === version) setLoading(false) }
  }
  async function choose(row: Session) {
    try { await open(row); reason.current = 'selection'; close() }
    catch { await read(0, generation.current); setError('此话题已不可访问，结果已刷新。') }
  }
  useEffect(() => {
    const version = ++generation.current
    setRows(query ? [] : recent || []); setSelected(0); setMore(false); setLoading(true)
    const timer = query ? setTimeout(() => void read(0, version), 180) : undefined
    if (!query) void read(0, version)
    return () => { clearTimeout(timer); generation.current++ }
  }, [query])
  useEffect(() => { list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' }) }, [selected])
  return <Dialog title="搜索话题" trigger={trigger} close={close} wide focusReturnMode="search" closeReason={reason}>
    <div className={s.searchBody} onKeyDown={event => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setSelected(index => Math.max(0, Math.min(rows.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))) }
      if (event.key === 'Enter' && rows[selected]) { event.preventDefault(); void choose(rows[selected]) }
    }}>
      <label className={s.search}><Search size={20} /><input data-first role="combobox" aria-controls="search-results" aria-expanded="true" aria-activedescendant={rows[selected] ? 'result-' + rows[selected].id : undefined} aria-label="搜索标题或聊天内容" maxLength={100} placeholder="搜索标题或聊天内容" value={query} onChange={event => setQuery(event.target.value)} /></label>
      <p className={s.subtle}>{query ? '匹配的话题' : '近期话题'}</p>
      <div ref={list} id="search-results" role="listbox" className={s.searchResults}>
        {rows.map((row, index) => <button id={'result-' + row.id} role="option" aria-selected={index === selected} className={s.searchResult} key={row.id} onMouseEnter={() => setSelected(index)} onClick={() => void choose(row)}>
          <strong>{excerpt(row.title, query)}{row.state === 'archived' && <small> · 已封存</small>}</strong><span>{excerpt(row.match_excerpt || row.display_abstract || '尚无展示摘要', query)}</span>
        </button>)}
        {!rows.length && loading && <p role="status">正在读取话题</p>}
        {!loading && !error && !rows.length && <p>没有匹配的话题。</p>}
      </div>
      <div className={s.searchStatus}>{error ? <p className={s.failureText} role="alert">{error}<button onClick={() => void read(0, generation.current)} className={s.textButton}>重试</button></p> : more ? <button disabled={loading} className={s.textButton} onClick={() => void read(rows.length, generation.current)}>继续加载</button> : loading && rows.length > 0 && <span role="status">正在读取话题</span>}</div>
    </div>
  </Dialog>
}
