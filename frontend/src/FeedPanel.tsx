import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { BookOpen, Code2, FlaskConical, MessageCircle, Orbit, Radio, Sparkles } from 'lucide-react'
import { api, time, type FeedImage, type Item } from './data'
import { Markdown } from './Markdown'
import s from './App.module.css'

type Page = { items: Item[]; next_cursor: string | null }
const icons = { ai: Sparkles, code: Code2, space: Orbit, science: FlaskConical, learning: BookOpen, general: Radio }
const byDate = (a: Item, b: Item) => b.created - a.created || (a.id < b.id ? 1 : a.id > b.id ? -1 : 0)
const date = (created: number) => new Date(created * 1000).toLocaleDateString('zh-CN', { timeZone: 'Asia/Taipei', year: 'numeric', month: 'long', day: 'numeric' })

export function FeedPanel({ snapshot, header, active, layoutKey, scrollRef, initialTop, position, discuss, detail, showImage }: { snapshot: Item[]; header: ReactNode; active: boolean; layoutKey: string; scrollRef: RefObject<HTMLElement | null>; initialTop: number; position: (top: number) => void; discuss: (row: Item) => void; detail: (row: Item) => void; showImage: (image: FeedImage, trigger: HTMLElement) => void }) {
  const [rows, setRows] = useState<Item[]>(snapshot)
  const [cursor, setCursor] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')
  const [failed, setFailed] = useState<string[]>([])
  const loading = useRef(false)
  const anchor = useRef<{ id?: string; offset?: number; top: number }>({ top: initialTop })

  function capture() {
    const node = scrollRef.current
    if (!node || !active || !node.clientWidth || !node.clientHeight) return
    const top = node.getBoundingClientRect().top
    const item = Array.from(node.querySelectorAll<HTMLElement>('[data-feed-id]')).find(item => item.getBoundingClientRect().bottom > top)
    anchor.current = { id: item?.dataset.feedId, offset: item ? item.getBoundingClientRect().top - top : 0, top: node.scrollTop }
    position(node.scrollTop)
  }
  function restore() {
    const node = scrollRef.current
    if (!node || !active || !node.clientWidth || !node.clientHeight) return
    const saved = anchor.current
    const item = saved.id ? node.querySelector<HTMLElement>('[data-feed-id="' + CSS.escape(saved.id) + '"]') : null
    if (item) node.scrollTop += item.getBoundingClientRect().top - node.getBoundingClientRect().top - (saved.offset || 0)
    else node.scrollTop = saved.top
  }
  function merge(items: Item[]) {
    capture()
    setRows(prior => Array.from(new Map([...prior, ...items].map(row => [row.id, row])).values()).sort(byDate))
  }
  async function load(first = false) {
    if (loading.current || (!first && !cursor)) return
    loading.current = true; setBusy(true); setError('')
    try {
      const page = await api<Page>('/feed/items?limit=20' + (first || !cursor ? '' : '&before=' + encodeURIComponent(cursor)))
      merge(page.items); setCursor(page.next_cursor); setReady(true)
    } catch (error) { setError((error as Error).message) }
    finally { loading.current = false; setBusy(false) }
  }
  useEffect(() => { void load(true) }, [])
  useEffect(() => { merge(snapshot) }, [snapshot])
  useLayoutEffect(() => { restore() }, [rows, active, layoutKey])
  useEffect(() => {
    const node = scrollRef.current
    if (!node) return
    const resize = new ResizeObserver(() => restore())
    resize.observe(node)
    return () => resize.disconnect()
  }, [active])
  useEffect(() => {
    const node = scrollRef.current
    if (!node) return
    const scroll = () => { capture(); if (active && ready && cursor && !error && node.scrollHeight - node.scrollTop - node.clientHeight < 300) void load() }
    node.addEventListener('scroll', scroll)
    return () => node.removeEventListener('scroll', scroll)
  }, [active, ready, cursor, error])

  let previousDate = ''
  return <section ref={scrollRef} className={s.feedPanel} hidden={!active} aria-label="动态内容">
    <div className={s.feedContent}>{header}
      {rows.map(row => {
        const group = date(row.created), heading = group !== previousDate
        previousDate = group
        const key = row.payload.icon_key && row.payload.icon_key in icons ? row.payload.icon_key as keyof typeof icons : 'general'
        const Icon = icons[key], picture = row.payload.images?.[0], source = row.payload.sources?.find(source => source.url)
        return <div key={row.id}>{heading && <h2 className={s.feedDate}>{group}</h2>}<article className={s.feedItem} data-feed-id={row.id}>
          <div className={s.feedHeading}><span className={s.feedIcon + ' ' + s['feedIcon_' + key]}><Icon size={20} /></span><button className={s.feedTitle} onClick={() => detail(row)}>{row.payload.title}</button></div>
          <div className={s.feedBody}><Markdown feed>{row.payload.body || ''}</Markdown></div>
          {picture && <div className={s.feedMedia} style={{ aspectRatio: picture.width && picture.height ? `${picture.width}/${picture.height}` : '16/9' }}>{failed.includes(picture.asset_id) ? <p className={s.subtle}>配图暂时无法读取</p> : <button aria-label={'查看配图 ' + row.payload.title} onClick={event => showImage(picture, event.currentTarget)}><img src={'/api/feed-media/' + picture.asset_id} alt={picture.alt || row.payload.title} loading="lazy" onError={() => setFailed(prior => [...prior, picture.asset_id])} /></button>}</div>}
          <div className={s.feedFooter}><button className={s.feedDiscuss} onClick={() => discuss(row)}><MessageCircle size={18} />讨论</button>{source ? <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a> : row.payload.source_url ? <a href={row.payload.source_url} target="_blank" rel="noreferrer">查看来源</a> : <span>{row.payload.source || 'Navi 本机事项'}</span>}</div>
          <details className={s.feedDetails}><summary>来源与时间</summary><p>生成 {time(row.created)}{row.payload.checked_at && ' · 检查 ' + time(row.payload.checked_at)}</p>{row.payload.sources?.map(source => <p key={source.id}>{source.url ? <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a> : source.title}{source.source_time ? ' · 发布 ' + time(source.source_time) : source.published_at ? ' · 发布 ' + source.published_at : ''}{source.read_scope && ' · ' + (source.read_scope === 'article' ? '读取原文正文' : '读取订阅正文')}{source.read_at && ' · 读取 ' + time(source.read_at)}</p>)}</details>
        </article></div>
      })}
      {!rows.length && !busy && <p className={s.empty}>还没有关注内容。设置关注说明后会在每日更新时整理相关材料。</p>}
      {error && <p role="alert" className={s.failureText}>{error}，已读内容仍保留。</p>}
      {(cursor || !ready || error) && <button className={s.secondary + ' ' + s.feedMore} disabled={busy} onClick={() => void load(!ready)}>{busy ? '正在加载…' : error ? '重试加载' : '加载更多'}</button>}
      {ready && !cursor && rows.length > 0 && <p className={s.feedEnd}>已读完当前动态</p>}
    </div>
  </section>
}
