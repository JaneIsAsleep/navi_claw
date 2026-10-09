import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowUp, BookOpen, Check, ChevronRight, ChevronLeft, Heart, Users, Wallet, Briefcase, Palette, Zap, Copy, Download, FileText, Flag, Lightbulb, Menu, MessageCircle, MoreHorizontal, Plus, Radio, Search, Settings, Square, X } from 'lucide-react'
import { api, labels, localDate, terminal, time, type Action, type CheckItem, type Config, type FeedImage, type GoalSetup, type GoalCard, type Detail, type Editor, type Item, type Job, type Message, type Reference, type Schedule, type Session, type State, type Tab } from './data'
import { Dialog } from './Dialog'
import { SearchDialog } from './SearchDialog'
import { SourcesDialog } from './SourcesDialog'
import { IdeaDialog } from './IdeaDialog'
import { ChatPanel } from './ChatPanel'
import { FeedPanel } from './FeedPanel'
import { GoalChoices } from './GoalChoices'
import { ActionCard } from './ActionCard'
import { AssistantDetails } from './AssistantDetails'
import { Markdown } from './Markdown'
import s from './App.module.css'
import naviMark from './navi-mark.svg'

const nav = [{ key: 'chat', label: '对话', icon: MessageCircle }, { key: 'search', label: '搜索', icon: Search }, { key: 'feed', label: '动态消息', icon: Radio }, { key: 'idea', label: '构想', icon: Lightbulb }, { key: 'goal', label: '目标', icon: Flag }, { key: 'artifact', label: '成果库', icon: FileText }] as const
type Page = typeof nav[number]['key'] | 'settings'
const pageFromHash = (): Page => nav.some(row => row.key === location.hash.slice(1)) || location.hash === '#settings' ? location.hash.slice(1) as Page : 'chat'
const initialId = localStorage.getItem('navi-draft-active') === 'true' ? '' : localStorage.getItem('navi-session') || ''
function draft(id: string): { text: string; references: Reference[]; kind: string } {
  try { return { kind: 'chat', ...JSON.parse(localStorage.getItem('navi-draft-' + (id || 'new')) || '{"text":"","references":[]}') } } catch { return { text: '', references: [], kind: 'chat' } }
}
function readingPositions(): Record<string, number> {
  try { return JSON.parse(localStorage.getItem('navi-reading-positions') || '{}') } catch { return {} }
}
const eventLabels: Record<string, string> = { 'record.saved': '目标已保存或更新', 'schedule.created': '已安排关联提醒', 'schedule.updated': '关联提醒已调整', 'schedule.triggered': '约定时间已到', 'job.queued': '工作已排队', 'job.started': '开始处理', 'job.succeeded': '结果已生成', 'job.failed': '执行失败', 'job.cancelled': '工作已取消', 'delivery.completed': '已实际投递', 'action.proposed': '等待审核具体行动', 'action.decided': '审核决定已保存' }

export default function App() {
  const [state, setState] = useState<State | null>(null)
  const [page, setPage] = useState<Page>(() => pageFromHash() === 'search' ? 'chat' : pageFromHash())
  const [sessionId, setSessionId] = useState(initialId)
  const [openedSession, setOpenedSession] = useState<Session | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState(draft(initialId).text)
  const [references, setReferences] = useState<Reference[]>(draft(initialId).references)
  const [composerKind, setComposerKind] = useState(draft(initialId).kind)
  const [connected, setConnected] = useState(false)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [stream, setStream] = useState({ id: '', text: '', status: '' })
  const [navOpen, setNavOpen] = useState(false)
  const [sessionOpen, setSessionOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(location.hash === '#search')
  const [recentSearch, setRecentSearch] = useState<Session[] | null>(null)
  const [searchTarget, setSearchTarget] = useState<{ session: string; message: string } | null>(null)
  const searchLocated = useRef('')
  const [idea, setIdea] = useState<Item | null>(null)
  const [sourcesOpen, setSourcesOpen] = useState(false)
  const [completedOpen, setCompletedOpen] = useState(false)
  const [goalCategory, setGoalCategory] = useState<{ id: string; label: string } | null>(null)
  const [completedRows, setCompletedRows] = useState<Record<string, Item>>({})
  const completionTimers = useRef<Record<string, number>>({})
  const goalStarting = useRef(false)
  const [goalSession, setGoalSession] = useState(localStorage.getItem('navi-goal-session') || '')
  const [goalChatVisible, setGoalChatVisible] = useState(localStorage.getItem('navi-goal-chat-visible') === 'true')
  const [feedChatVisible, setFeedChatVisible] = useState(false)
  const [pairedView, setPairedView] = useState<'content' | 'chat'>('chat')
  const [expandedFrom, setExpandedFrom] = useState<'goal' | 'feed' | null>(null)
  const [feedReply, setFeedReply] = useState<{ id: string; title: string; session: string } | null>(() => { try { return JSON.parse(localStorage.getItem('navi-feed-reply') || 'null') } catch { return null } })
  const [feedImage, setFeedImage] = useState<FeedImage | null>(null)
  const [pageMenu, setPageMenu] = useState(false)
  const searchTrigger = useRef<HTMLElement | null>(null)
  const [assistant, setAssistant] = useState(false)
  const [tab, setTab] = useState<Tab>('activity')
  const [memory, setMemory] = useState(false)
  const [memoryId, setMemoryId] = useState('')
  const [editor, setEditor] = useState<Editor | null>(null)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [audit, setAudit] = useState<unknown>(null)
  const [menuId, setMenuId] = useState('')
  const [deletion, setDeletion] = useState<{ session?: Session; reminders?: { id: string; title: string; kind: string }[]; item?: Item; check?: CheckItem } | null>(null)
  const [config, setConfig] = useState<Config | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [connectionJob, setConnectionJob] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const chatRef = useRef<HTMLElement>(null)
  const contentRef = useRef<HTMLElement>(null)
  const feedRef = useRef<HTMLElement>(null)
  const feedImageTrigger = useRef<HTMLElement | null>(null)
  const initialized = useRef(false)
  const trigger = useRef<HTMLElement | null>(null)
  const interaction = useRef<HTMLElement | null>(null)
  const assistantTrigger = useRef<HTMLElement | null>(null)
  const menuTrigger = useRef<HTMLElement | null>(null)
  const positions = useRef<Record<string, number>>(readingPositions())
  const loadedSession = useRef('')
  const poll = useRef<() => Promise<void>>(async () => {})
  const goalSetup: GoalSetup = (() => { try { return JSON.parse((state?.sessions.find(row => row.id === sessionId) || openedSession)?.goal_setup_json || '{}') } catch { return {} } })()
  const goalPaired = page === 'goal' && goalChatVisible && goalSession === sessionId && Boolean(goalSession)
  const feedPaired = page === 'feed' && feedChatVisible && !detail
  const paired = goalPaired || feedPaired
  const resumableGoal = state?.sessions.find(row => row.id === goalSession && JSON.parse(row.goal_setup_json || '{}').category) || state?.sessions.find(row => JSON.parse(row.goal_setup_json || '{}').category)
  const currentSession = state?.sessions.find(row => row.id === sessionId) || openedSession
  const archived = currentSession?.state === 'archived'
  const busy = state?.jobs.find(job => job.session_id === sessionId && !terminal(job.status) && ['chat', 'ideas', 'draft', 'compact', 'connection', 'goal_setup', 'goal_save'].includes(job.kind))
  const pending = state?.actions.filter(row => row.status === 'pending').length || 0
  const modalOpen = Boolean(editor || deletion || audit)
  const overlayOpen = modalOpen || searchOpen || !!idea || sourcesOpen || completedOpen || !!goalCategory || !!feedImage

  function saveDraft(text: string, refs = references, kind = composerKind) {
    setInput(text); setReferences(refs); setComposerKind(kind)
    if (feedReply?.session === sessionId && !refs.some(ref => ref.id === feedReply.id)) setFeedReply(null)
    localStorage.setItem('navi-draft-' + (sessionId || 'new'), JSON.stringify({ text, references: refs, kind }))
  }
  const positionKey = page === 'chat' ? 'chat-' + sessionId : detail ? page + '-' + detail.id : page
  function rememberPosition() { positions.current[positionKey] = (page === 'chat' ? chatRef.current : page === 'feed' && !detail ? feedRef.current : contentRef.current)?.scrollTop || 0; if (paired) positions.current['chat-' + sessionId] = chatRef.current?.scrollTop || 0; localStorage.setItem('navi-reading-positions', JSON.stringify(positions.current)) }
  function navigate(next: Page) {
    if (next === 'search') { searchTrigger.current = interaction.current || document.activeElement as HTMLElement; setSearchOpen(true); return }
    rememberPosition(); setPageMenu(false); setPage(next); location.hash = next; setNavOpen(false); setSessionOpen(false); setDetail(null); setError(''); setNotice('')
  }
  function chooseSession(row: Session) {
    setSearchTarget(null); searchLocated.current = ''
    rememberPosition()
    const next = draft(row.id); setInput(next.text); setReferences(next.references); setComposerKind(next.kind); setMenuId('')
    loadedSession.current = ''; setSessionId(row.id); setOpenedSession(row); setMessages([])
    if (JSON.parse(row.goal_setup_json || '{}').category) { setGoalSession(row.id); localStorage.setItem('navi-goal-session', row.id) }
    setNotice(''); setError('')
    localStorage.setItem('navi-session', row.id); localStorage.setItem('navi-draft-active', 'false')
    setPage('chat'); location.hash = 'chat'; setAssistant(false); setSessionOpen(false); setNavOpen(false); setDetail(null)
  }
  async function openSession(id: string) {
    await act(async () => chooseSession(await api<Session>('/sessions/' + id)))
  }
  async function chooseSearch(row: Session) {
    const value = await api<Message[]>('/sessions/' + row.id + '/messages')
    chooseSession(row)
    loadedSession.current = row.id; setMessages(value)
    if (row.match_message_id) setSearchTarget({ session: row.id, message: row.match_message_id })
  }
  async function cacheRecent() {
    try { setRecentSearch(await api<Session[]>('/sessions/search?recent=true&scope=all&limit=20')) } catch { setRecentSearch(null) }
  }
  async function refresh() {
    try {
      const value = await api<State>('/state?feed_limit=20')
      setState(value); setConnected(true); setConfig(current => current || value.settings)
      if (!initialized.current) {
        initialized.current = true
        if (localStorage.getItem('navi-draft-active') !== 'true') {
          const recent = value.sessions.find(row => row.id === initialId)
          const row = recent || value.sessions.find(row => row.id === value.main_session_id)!
          if (initialId && !recent) setNotice('最近的话题已封存、删除或不可用，已返回主对话。')
          const saved = draft(row.id)
          setSessionId(row.id); setOpenedSession(row); setInput(saved.text); setReferences(saved.references); setComposerKind(saved.kind)
          localStorage.setItem('navi-session', row.id)
        }
      }
    } catch { setConnected(false) }
  }
  useEffect(() => { localStorage.setItem('navi-goal-chat-visible', String(goalChatVisible)) }, [goalChatVisible])
  useEffect(() => { if (feedReply) localStorage.setItem('navi-feed-reply', JSON.stringify(feedReply)); else localStorage.removeItem('navi-feed-reply') }, [feedReply])
  useEffect(() => {
    if (page !== 'goal') {
      Object.values(completionTimers.current).forEach(clearTimeout)
      completionTimers.current = {}; setCompletedRows({})
    }
    return () => { Object.values(completionTimers.current).forEach(clearTimeout); completionTimers.current = {} }
  }, [page])
  useEffect(() => { const frame = requestAnimationFrame(() => { if (!searchTarget && chatRef.current) chatRef.current.scrollTop = positions.current['chat-' + sessionId] ?? chatRef.current.scrollHeight }); return () => cancelAnimationFrame(frame) }, [page, goalChatVisible, feedChatVisible, pairedView, sessionId])
  poll.current = refresh
  useEffect(() => {
    if (!connectionJob) return
    const row = state?.jobs.find(job => job.id === connectionJob)
    if (row && terminal(row.status)) { setNotice(row.status === 'succeeded' ? '真实 API 检查成功，已收到模型响应。' : '检查失败：' + row.error); setConnectionJob('') }
  }, [connectionJob, state?.jobs])
  useEffect(() => { void refresh(); const timer = setInterval(() => void poll.current(), 2500); return () => clearInterval(timer) }, [])
  useEffect(() => { void cacheRecent() }, [state?.sessions.map(row => row.id + row.state + row.title).join('|')])
  useEffect(() => {
    if (!searchTarget) return
    const timer = setTimeout(() => setSearchTarget(null), 5000)
    return () => clearTimeout(timer)
  }, [searchTarget])
  useEffect(() => {
    if (!searchTarget || searchTarget.session !== sessionId || searchLocated.current === searchTarget.message) return
    const frame = requestAnimationFrame(() => {
      const node = chatRef.current?.querySelector<HTMLElement>('[data-message-id="' + CSS.escape(searchTarget.message) + '"]')
      if (node) { node.scrollIntoView({ block: 'center' }); searchLocated.current = searchTarget.message }
    })
    return () => cancelAnimationFrame(frame)
  }, [messages, searchTarget, sessionId])
  useEffect(() => {
    if (!sessionId) { setMessages([]); return }
    let live = true
    void api<Message[]>('/sessions/' + sessionId + '/messages').then(value => {
      if (!live) return
      const first = loadedSession.current !== sessionId
      const node = chatRef.current
      const atBottom = node ? node.scrollHeight - node.scrollTop - node.clientHeight < 100 : false
      loadedSession.current = sessionId; setMessages(value)
      requestAnimationFrame(() => { if (live && (page === 'chat' || paired) && chatRef.current) {
        if (searchTarget?.session === sessionId) return
        if (first) chatRef.current.scrollTop = positions.current['chat-' + sessionId] ?? chatRef.current.scrollHeight
        else if (atBottom) chatRef.current.scrollTop = chatRef.current.scrollHeight
      } })
    }).catch(() => {
      if (live && state) { const main = state.sessions.find(row => row.id === state.main_session_id)!; chooseSession(main); setNotice('原话题已不可用，已返回主对话。') }
    })
    return () => { live = false }
  }, [sessionId, state?.jobs, state?.actions])
  useEffect(() => {
    if ((page === 'chat' || paired) && sessionId && messages.some(row => row.unread)) void api('/sessions/' + sessionId + '/read', 'POST', {})
  }, [page, sessionId, messages])
  useEffect(() => {
    if (!busy) return
    const events = new EventSource('/api/jobs/' + busy.id + '/events')
    setStream(current => current.id === busy.id ? current : { id: busy.id, text: '', status: '正在连接执行进度' })
    events.addEventListener('delta', event => { const data = JSON.parse((event as MessageEvent).data); setStream(current => ({ ...current, text: current.text + data.text })) })
    events.addEventListener('status', event => { const data = JSON.parse((event as MessageEvent).data); setStream(current => ({ ...current, status: data.message || labels[data.status] })) })
    events.addEventListener('done', () => { events.close(); setStream({ id: '', text: '', status: '' }); void refresh() })
    events.onerror = () => setStream(current => ({ ...current, status: '连接暂时中断，工作状态保存在本机' }))
    return () => events.close()
  }, [busy?.id])
  useEffect(() => { const node = chatRef.current; if (!searchTarget && node && node.scrollHeight - node.scrollTop - node.clientHeight < 200) node.scrollTop = node.scrollHeight }, [messages.length, stream.text])
  useEffect(() => { const frame = requestAnimationFrame(() => { const node = page === 'chat' ? chatRef.current : contentRef.current; if (!searchTarget && node) node.scrollTop = positions.current[positionKey] || 0 }); return () => cancelAnimationFrame(frame) }, [positionKey])
  useEffect(() => {
    const change = () => { const next = pageFromHash(); if (next === 'search') { setPage('chat'); setSearchOpen(true); return } if (next !== page) { rememberPosition(); setPage(next); setDetail(null) } }
    window.addEventListener('hashchange', change); return () => window.removeEventListener('hashchange', change)
  }, [page, sessionId])
  useEffect(() => {
    if (!menuId) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') { setMenuId(''); menuTrigger.current?.focus() } }
    document.addEventListener('keydown', close); return () => document.removeEventListener('keydown', close)
  }, [menuId])
  useEffect(() => {
    if (!navOpen) return
    const opener = interaction.current
    document.getElementById('close-navigation')?.focus()
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setNavOpen(false) }
    document.addEventListener('keydown', close)
    return () => { document.removeEventListener('keydown', close); requestAnimationFrame(() => opener?.focus()) }
  }, [navOpen])

  async function act(operation: () => Promise<unknown>, message = '') {
    setWorking(true); setError(''); setNotice('')
    try { await operation(); await refresh(); if (message) setNotice(message) } catch (failure) { setError((failure as Error).message) } finally { setWorking(false) }
  }
  function actPath(path: string, method: string, data: unknown, message = '') { void act(async () => { await api(path, method, data); if (detail && path === '/records/' + detail.id) setDetail(await api<Detail>(path)) }, message) }
  function capture() { trigger.current = menuId ? menuTrigger.current : interaction.current || document.activeElement as HTMLElement }
  function closeModal() { if (working && deletion) return; setEditor(null); setDeletion(null); setAudit(null); setError('') }
  function showAssistant(next = 'activity' as Tab, showMemory = false, id = '') {
    assistantTrigger.current = interaction.current || document.activeElement as HTMLElement
    setTab(next); setMemory(showMemory); setMemoryId(id); setAssistant(true)
  }
  function newChat() {
    setNotice(''); setError('')
    rememberPosition(); loadedSession.current = ''; setSessionId(''); setOpenedSession(null); setMessages([]); setPage('chat'); location.hash = 'chat'
    const next = draft(''); setInput(next.text); setReferences(next.references); setComposerKind(next.kind)
    localStorage.setItem('navi-draft-active', 'true'); setSessionOpen(false); requestAnimationFrame(() => inputRef.current?.focus())
  }
  async function submit(text = input, kind = composerKind) {
    if (!text.trim() || working || busy || archived) return
    await act(async () => {
      const row = await api<Job>('/chat', 'POST', { text, session_id: sessionId || undefined, kind, references, new_session: !sessionId })
      localStorage.removeItem('navi-draft-' + (sessionId || 'new')); localStorage.setItem('navi-draft-active', 'false'); localStorage.setItem('navi-session', row.session_id!)
      setSessionId(row.session_id!); setInput(''); setReferences([]); setFeedReply(null); setComposerKind('chat'); if (!paired) { setPage('chat'); location.hash = 'chat' }
      positions.current['chat-' + row.session_id] = Number.MAX_SAFE_INTEGER
    })
  }
  function discuss(row: Item, prompt = '请结合引用内容，和我讨论下一步。', kind = 'chat') {
    if (row.kind === 'feed') { discussFeed(row); return }
    rememberPosition()
    const ref: Reference = { id: row.id, kind: row.kind, title: row.payload.title, body: row.payload.body || row.payload.next_step || '', ...(row.payload.source_url ? { source_url: row.payload.source_url } : {}) }
    const sourceId = row.source_session?.id || row.payload.session_id
    const existing = sourceId ? state?.sessions.find(session => session.id === sourceId) : undefined
    if (existing) {
      const saved = draft(existing.id); chooseSession(existing)
      const refs = [...saved.references.filter(r => r.id !== row.id), ref].slice(-5)
      const text = saved.text ? saved.text + '\n\n' + prompt : prompt
      setInput(text); setReferences(refs); setComposerKind(kind); localStorage.setItem('navi-draft-' + existing.id, JSON.stringify({ text, references: refs, kind }))
    } else {
      newChat(); const saved = draft('')
      const refs = [...saved.references.filter(r => r.id !== row.id), ref].slice(-5)
      const text = saved.text ? saved.text + '\n\n' + prompt : prompt
      setInput(text); setReferences(refs); setComposerKind(kind); localStorage.setItem('navi-draft-new', JSON.stringify({ text, references: refs, kind }))
    }
    setAssistant(false); setDetail(null); requestAnimationFrame(() => inputRef.current?.focus())
  }
  function feedSession() {
    return currentSession?.state === 'active' && !JSON.parse(currentSession.goal_setup_json || '{}').category ? currentSession : state?.sessions.find(row => row.id === state.main_session_id)
  }
  function discussFeed(row: Item) {
    const target = feedSession()
    if (!target) return
    rememberPosition()
    const saved = target.id === sessionId ? { text: input, references, kind: composerKind } : draft(target.id)
    const prior = feedReply?.session === target.id ? feedReply.id : ''
    const refs = saved.references.filter(ref => ref.id !== prior && ref.id !== row.id)
    if (target.id !== sessionId) chooseSession(target)
    setFeedChatVisible(true); setPairedView('chat'); setPage('feed'); location.hash = 'feed'; setDetail(null); setAssistant(false)
    if (refs.length >= 5) { setError('引用已达五条，请先移除一条再讨论。'); return }
    const ref: Reference = { id: row.id, kind: 'feed', title: row.payload.title, body: row.payload.body || '', source_ids: row.payload.source_ids || [], ...(row.payload.source_url ? { source_url: row.payload.source_url } : {}) }
    const next = [...refs, ref]
    setInput(saved.text); setReferences(next); setComposerKind(saved.kind); setFeedReply({ id: row.id, title: row.payload.title, session: target.id }); setError('')
    localStorage.setItem('navi-draft-' + target.id, JSON.stringify({ text: saved.text, references: next, kind: saved.kind }))
    requestAnimationFrame(() => inputRef.current?.focus({ preventScroll: true }))
  }
  function toggleFeedChat() {
    rememberPosition()
    if (feedPaired) { setFeedChatVisible(false); return }
    const target = feedSession()
    if (!target) return
    if (target.id !== sessionId) chooseSession(target)
    setFeedChatVisible(true); setPairedView('chat'); setPage('feed'); location.hash = 'feed'; setDetail(null)
  }
  function toggleChatLayout() {
    rememberPosition()
    if (paired) { setExpandedFrom(feedPaired ? 'feed' : 'goal'); setPage('chat'); location.hash = 'chat' }
    else {
      const destination = expandedFrom || 'goal'
      setPage(destination); location.hash = destination; setDetail(null); setPairedView('chat')
      if (destination === 'feed') setFeedChatVisible(true)
      else setGoalChatVisible(true)
    }
  }
  async function openRecord(row: Item) { if (row.kind === 'idea') { capture(); setIdea(row); return } rememberPosition(); await act(async () => { const next = await api<Detail>('/records/' + row.id); const target = ['goal', 'idea', 'artifact'].includes(row.kind) ? row.kind as Page : 'feed'; setPage(target); location.hash = target; setDetail(next); setAssistant(false) }) }
  function edit(kind: Editor['kind'], row?: Item | Schedule | Action | CheckItem | Session) {
    capture()
    const data = row && 'payload' in row ? row.payload : row || {}
    const values: Record<string, string> = Object.fromEntries(Object.entries(data).map(([key, value]) => [key, String(value ?? '')]))
    if (!row) Object.assign(values, { title: '', body: '', next_step: '', state: 'active' })
    if (kind === 'task') Object.assign(values, { kind: row ? (row as Schedule).kind : 'once', instruction: row ? (row as Schedule).instruction : '', minutes: String(row ? ((row as Schedule).interval_seconds || 3600) / 60 : 60), next_due: localDate(row ? (row as Schedule).next_due : Date.now() / 1000 + 3600), goal_id: row ? (row as Schedule).goal_id || '' : '' })
    if (kind === 'check') Object.assign(values, { priority: String((row as CheckItem | undefined)?.priority || 2), due_at: (row as CheckItem | undefined)?.due_at ? localDate((row as CheckItem).due_at!) : '' })
    if (kind === 'brief') values.body = state?.feed_brief || ''
    setEditor({ kind, id: row?.id, values })
  }
  async function saveEditor(event: FormEvent) {
    event.preventDefault(); if (!editor || !state) return
    const current = editor
    await act(async () => {
      const v = current.values
      if (current.kind === 'session') {
        const row = await api<Session>('/sessions/' + current.id, 'PATCH', { title: v.title }); if (row.id === sessionId) setOpenedSession(row)
      } else if (current.kind === 'brief') { await api('/settings', 'PUT', { feed_brief: v.body }); setConfig(c => c ? { ...c, feed_brief: v.body } : c) }
      else if (current.kind === 'source') await api('/feed-sources', 'POST', { source_id: v.source_id || state.feed_catalog[0]?.id })
      else if (current.kind === 'task') {
        const fields = { title: v.title, instruction: v.instruction, interval_seconds: Number(v.minutes) * 60, next_due: new Date(v.next_due).getTime() / 1000 }
        await api(current.id ? '/schedules/' + current.id : '/schedules', current.id ? 'PATCH' : 'POST', current.id ? fields : { ...fields, kind: v.kind, session_id: state.main_session_id, goal_id: v.goal_id || undefined })
      } else if (current.kind === 'check') {
        const item: CheckItem = { id: current.id || crypto.randomUUID(), title: v.title, body: v.body, priority: Number(v.priority), due_at: v.due_at ? new Date(v.due_at).getTime() / 1000 : null, source_url: v.source_url, done: v.done === 'true' }
        await api('/check-items', 'PUT', current.id ? state.check_items.map(row => row.id === current.id ? item : row) : [...state.check_items, item])
      } else if (current.kind === 'action') await api('/actions/' + current.id, 'PUT', { title: v.title, target: v.target, body: v.body })
      else {
        const old = state.records.find(row => row.id === current.id)?.payload
        await api(current.id ? '/records/' + current.id : '/records/' + current.kind, current.id ? 'PUT' : 'POST', { ...old, title: v.title, body: v.body, ...(current.kind === 'memory' ? { enabled: old?.enabled ?? true } : { state: v.state, next_step: v.next_step }) })
        if (detail?.id === current.id) setDetail(await api<Detail>('/records/' + current.id))
      }
      setEditor(null)
    }, current.kind === 'brief' ? '关注说明已保存，只影响以后生成的内容。' : '已保存')
  }
  function decide(row: Action, decision: string) { actPath('/actions/' + row.id + '/decision', 'POST', { decision, content_hash: row.content_hash }, decision === 'approve' ? '已批准，实际执行状态会继续更新。' : '已拒绝，此动作未执行。') }
  function removeItem(row: Item | CheckItem) { capture(); setDeletion('kind' in row ? { item: row } : { check: row }) }
  async function removeSession(row: Session) {
    capture(); setMenuId('')
    let impact: NonNullable<typeof deletion> | null = null
    await act(async () => { impact = await api('/sessions/' + row.id + '/deletion-impact') })
    if (impact) setDeletion(impact)
  }
  async function history(path: string) { capture(); await act(async () => setAudit(await api(path))) }
  async function startGoal(category: string) {
    if (working || goalStarting.current) return
    goalStarting.current = true
    const key = 'navi-goal-start-' + category
    const requestId = sessionStorage.getItem(key) || crypto.randomUUID()
    sessionStorage.setItem(key, requestId)
    await act(async () => {
      const row = await api<{session_id: string; job_id: string}>('/goals/start','POST',{category,request_id:requestId})
      chooseSession(await api<Session>('/sessions/' + row.session_id)); setGoalSession(row.session_id); localStorage.setItem('navi-goal-session',row.session_id)
      setGoalChatVisible(true); setPairedView('chat'); setPage('goal'); location.hash='goal'
      setGoalCategory(null)
      sessionStorage.removeItem(key)
    })
    goalStarting.current = false
  }
  function toggleGoalChat() {
    if (goalPaired) { setGoalChatVisible(false); return }
    if (!resumableGoal) return
    if (resumableGoal.id !== sessionId) chooseSession(resumableGoal)
    setGoalSession(resumableGoal.id); localStorage.setItem('navi-goal-session', resumableGoal.id)
    setGoalChatVisible(true); setPairedView('chat'); setPage('goal'); location.hash = 'goal'
  }
  async function completeGoal(row: Item) {
    if (working || completedRows[row.id]) return
    await act(async () => {
      await api('/goals/' + row.id + '/state', 'POST', { operation: 'complete' })
      setCompletedRows(current => ({ ...current, [row.id]: { ...row, payload: { ...row.payload, state: 'completed' } } }))
      completionTimers.current[row.id] = window.setTimeout(() => {
        setCompletedRows(current => { const next = { ...current }; delete next[row.id]; return next })
        delete completionTimers.current[row.id]
      }, 3000)
    })
  }
  async function chooseGoal(card: GoalCard, choiceId: string) {
    await act(async () => { await api('/sessions/' + sessionId + '/goal-choices','POST',{card_id:card.card_id,choice_id:choiceId,setup_version:card.setup_version}); setOpenedSession(await api<Session>('/sessions/' + sessionId)) })
  }
  function avatar() { return <button className={s.avatar} aria-label={'关于 Navi' + (pending ? '，' + pending + '项待审核' : '')} onClick={() => showAssistant()}><img alt="" aria-hidden="true" className={s.orb} src={naviMark} width={44} height={44} />{pending > 0 && <span className={s.avatarBadge}>{pending}</span>}</button> }
  const title = nav.find(row => row.key === page)?.label || '设置'
  const records = [...(state?.records.filter(row => row.kind === page && (page !== 'goal' || !['completed','cancelled'].includes(row.payload.state || 'active')) && (page !== 'feed' || row.payload.origin !== 'rss_material' && !row.payload.item_id?.toString().startsWith('rss-'))) || []), ...(page === 'goal' ? Object.values(completedRows) : [])].filter((row, index, rows) => rows.findIndex(other => other.id === row.id) === index).sort((a, b) => b.created - a.created)
  const trackingSchedules = state?.schedules.filter(row => !['completed','cancelled'].includes(row.state)) || []
  const trackingJobs = state?.jobs.filter(row => !row.task_id && !terminal(row.status) && !['feed','ideas','session_meta','goal_setup','goal_save'].includes(row.kind)) || []
  const sessionActions = state?.actions.filter(row => row.source_session_id === sessionId) || []

  const contentHeader = <>
          <div className={s.pageIntro}><div className={s.pageHeading}><h1>{title}</h1>{['feed','goal'].includes(page) && <div className={s.pageOptions}><button className={s.iconButton} aria-label={page === 'feed' ? '动态选项' : '目标选项'} aria-expanded={pageMenu} onClick={() => setPageMenu(!pageMenu)}><MoreHorizontal size={22} /></button>{pageMenu && <div className={s.optionsMenu}>{page === 'goal' ? <button onClick={() => { capture(); setPageMenu(false); setCompletedOpen(true) }}>查看已完成目标</button> : <><button onClick={() => { setPageMenu(false); edit('brief') }}>关注说明</button><button onClick={() => { capture(); setPageMenu(false); setSourcesOpen(true) }}>管理来源</button></>}</div>}</div>}</div><p>{({ search: '对话标题搜索，包含匹配的封存话题。', feed: '关注内容。按本地生成或收录顺序安静更新。', idea: '结合当时的目标与偏好，看看下一步。', goal: '方向、下一步和真实推进记录。', artifact: '查看和下载已经保存的成果。', settings: '连接模型，掌握授权和本机运行边界。' } as Record<string, string>)[page]}</p></div>
          {['feed','idea'].includes(page) && <><p className={s.subtle}>每日 06:00 更新，需本机运行。休眠或停机时，清醒后补更新，只补最近一次。</p>{page === 'feed' && !state?.feed_brief && <p className={s.subtle}>请在动态选项中设置关注说明。</p>}{page === "feed" && state?.records.some(row => row.id.startsWith("source-") && row.payload.enabled !== false && row.payload.last_error) && <button className={s.textButton} onClick={() => { capture(); setSourcesOpen(true) }}>来源读取失败，查看来源状态</button>}{(() => { const content = state?.auto_content?.[page === 'feed' ? 'feed' : 'ideas']; const latest = content?.latest; return <>{content?.last_success && <p className={s.subtle}>最近成功更新 {time(content.last_success)}</p>}{latest?.error && <p role="status" className={s.failureText}>{latest.error} · {latest.status === 'pending' ? '下次重试 ' + time(latest.next_run) + '，需持续清醒' : latest.status === 'running' ? '正在重试' : '已停止重试，等待下次日更'}</p>}</> })()}</>}
  </>
  const chatPanel = <ChatPanel avatar={avatar()} controls={<><button className={s.mobileMenu + ' ' + s.iconButton} aria-label="打开导航" onClick={() => setNavOpen(true)}><Menu size={22} /></button>{!paired && <button className={s.mobileSessions + ' ' + s.iconButton} aria-label="打开会话列表" onClick={() => setSessionOpen(!sessionOpen)}><MessageCircle size={21} /></button>}</>} split={paired} returnLabel={expandedFrom === 'feed' ? '返回并排动态' : '返回并排目标'} toggle={paired || expandedFrom || goalSession === sessionId && goalSession ? toggleChatLayout : undefined}>
        {paired && error && <div role="alert" className={s.error}>{error}</div>}{paired && notice && <div role="status" className={s.notice}>{notice}</div>}
        {archived && <div className={s.archiveBanner}>已封存。查看历史不会解封。<button className={s.textButton} onClick={() => void act(async () => { const row = await api<Session>('/sessions/' + sessionId, 'PATCH', { operation: 'unarchive' }); setOpenedSession(row) }, '已明确解封，未来提醒回此对话。')}>解封此对话</button></div>}
        <section ref={chatRef} className={s.chat} aria-label="对话内容" onScroll={() => { positions.current['chat-' + sessionId] = chatRef.current?.scrollTop || 0; localStorage.setItem('navi-reading-positions',JSON.stringify(positions.current)) }}>
          {!messages.length && !busy && !sessionActions.length ? <div className={s.welcome}><h1>{currentSession?.kind === 'main' ? '今天要继续哪件事？' : '从一件事开始。'}</h1><p>交代想做的事，或接回上次的话题。<br />需要保存或安排提醒时，直接告诉 Navi。</p>{!state?.settings.api_key_configured && <button className={s.secondary} onClick={() => navigate('settings')}>连接 DeepSeek</button>}</div> : <div className={s.messageList}>
            {messages.map((message, index) => <article key={message.id} data-message-id={message.id} className={s.message + ' ' + (message.role === 'user' ? s.user : s.assistant) + (searchTarget?.message === message.id ? ' ' + s.searchHit : '')}>
              <div className={s.speaker}>{message.role === 'user' ? '你' : 'Navi'}<time>{time(message.created)}{message.outbox_id && ' · 实际投递'}</time></div>
              <div className={s.bubble}><Markdown>{message.content}</Markdown>{message.references?.map(ref => <div className={s.savedReference} key={ref.id}>引用：{ref.title}{ref.source_url && <a href={ref.source_url} target="_blank" rel="noreferrer">查看来源</a>}</div>)}{message.interaction?.type === 'goal_choices' && <GoalChoices card={message.interaction} active={!archived && goalSetup.active_card_id === message.id && goalSetup.version === message.interaction.setup_version} working={working || !!busy} choose={id => void chooseGoal(message.interaction!,id)} />}</div>
              <div className={s.messageActions}><button className={s.iconButton} aria-label="复制消息" onClick={() => void act(async () => { await navigator.clipboard.writeText(message.content) }, '已复制')}><Copy size={16} /></button><button className={s.textButton} onClick={() => saveDraft(input, [...references, { id: message.id, kind: 'message', title: message.role === 'user' ? '你的消息' : 'Navi 回复', body: message.content }].slice(-5))}>引用讨论</button></div>
              {message.role === 'assistant' && !message.outbox_id && !messages.slice(index + 1).some(m => m.job_id === message.job_id && m.role === 'assistant' && !m.outbox_id) && sessionActions.filter(row => row.job_id === message.job_id).map(row => <ActionCard key={row.id} row={row} working={working} decide={decide} edit={row => edit('action', row)} openSource={id => void openSession(id)} history={path => void history(path)} />)}
              {message.role === 'assistant' && state?.records.filter(row => (row.payload.source_job_id === message.job_id || row.payload.job_id === message.job_id) && ['memory', 'goal', 'artifact'].includes(row.kind)).map(row => <button className={s.textButton} key={row.id} onClick={() => row.kind === 'memory' ? showAssistant('identity', true, row.id) : void openRecord(row)}>{row.kind === 'memory' ? '查看记忆' : row.kind === 'goal' ? '查看目标' : '查看成果'}：{row.payload.title}</button>)}
              {message.role === 'user' && state?.jobs.filter(job => job.id === message.job_id && terminal(job.status) && job.status !== 'succeeded').map(job => <p className={s.failureText} key={job.id}>{labels[job.status]}：{job.error}<button className={s.textButton} onClick={() => saveDraft(message.content, message.references || [])}>放回草稿</button></p>)}
            </article>)}
            {sessionActions.filter(row => !messages.some(message => message.role === 'assistant' && !message.outbox_id && message.job_id === row.job_id)).map(row => <article key={'proposal-' + row.id} className={s.message + ' ' + s.assistant}><div className={s.speaker}>Navi</div><p className={s.subtle}>此话题保留的行动提案，实际执行以状态为准。</p><ActionCard row={row} working={working} decide={decide} edit={row => edit('action', row)} openSource={id => void openSession(id)} history={path => void history(path)} /></article>)}
            {busy && <article className={s.message + ' ' + s.assistant}><div className={s.speaker}>Navi</div><div className={s.bubble}>{stream.text ? <Markdown>{stream.text}</Markdown> : <p>{stream.status || '正在处理'}</p>}</div><p role="status" className={s.subtle}>{stream.status}</p></article>}
          </div>}
        </section>
        <div className={s.composerArea}>
          {feedReply?.session === sessionId && references.some(ref => ref.id === feedReply.id) && <p className={s.feedReply}>正在回复 Navi · {feedReply.title}</p>}
          {references.length > 0 && <div className={s.references}>{references.map(ref => <div className={s.reference} key={ref.id}><details><summary>引用：{ref.title}</summary><p>{ref.body}</p>{ref.source_url && <a href={ref.source_url} target="_blank" rel="noreferrer">原始来源</a>}</details><button className={s.iconButton} aria-label={'移除引用 ' + ref.title} onClick={() => saveDraft(input, references.filter(r => r.id !== ref.id))}><X size={16} /></button></div>)}</div>}
          <form className={s.composer} onSubmit={event => { event.preventDefault(); void submit() }}><textarea ref={inputRef} aria-label="消息内容" placeholder={archived ? '解封后继续话题' : '和 Navi 说点什么'} disabled={archived} rows={2} value={input} onChange={event => saveDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit() } }} /><div className={s.composerTools}><span className={s.subtle}>{input ? '草稿保存在本机' : 'Enter 发送 · Shift + Enter 换行'}</span>{busy ? <button type="button" className={s.send} aria-label="停止当前作业" onClick={() => actPath('/jobs/' + busy.id + '/cancel', 'POST', {})}><Square size={16} fill="currentColor" /></button> : <button type="submit" className={s.send} aria-label="发送消息" disabled={!input.trim() || working || !connected || archived}><ArrowUp size={20} /></button>}</div></form>
        </div>
</ChatPanel>
  return <div className={s.app} onClickCapture={event => { const target = (event.target as Element).closest<HTMLElement>('button, a'); if (target) interaction.current = target }}>
    <a className={s.skipLink} href="#main-content" onClick={event => { event.preventDefault(); document.getElementById('main-content')?.focus() }}>跳到主要内容</a>
    {navOpen && <div className={s.scrim} aria-hidden="true" onClick={() => setNavOpen(false)} />}
    <aside className={s.sidebar + (navOpen ? ' ' + s.sidebarOpen : '')} aria-label="主导航" inert={assistant || overlayOpen || undefined}>
      <div className={s.brand}>{page !== 'chat' && avatar()}{navOpen && <button id="close-navigation" className={s.iconButton} aria-label="关闭导航" onClick={() => setNavOpen(false)}><X size={20} /></button>}</div>
      <nav>{nav.map(({ key, label, icon: Icon }) => <a key={key} href={'#' + key} aria-label={label} aria-current={page === key ? 'page' : undefined} className={s.navItem + (page === key ? ' ' + s.selected : '')} onClick={event => { event.preventDefault(); navigate(key) }}><Icon size={22} /><span>{label}</span></a>)}</nav>
      <div className={s.sidebarBottom}><a href="#settings" className={s.navItem + (page === 'settings' ? ' ' + s.selected : '')} aria-label="设置" onClick={event => { event.preventDefault(); setConfig(state?.settings || null); navigate('settings') }}><Settings size={22} /><span>设置</span></a><span className={connected ? s.onlineDot : s.offlineDot} title={connected ? '本机服务可访问' : '本机连接中断'} /></div>
    </aside>
    {page === 'chat' && <aside className={s.sessions + (sessionOpen ? ' ' + s.sessionsOpen : '')} aria-label="会话列表" inert={assistant || overlayOpen || navOpen || undefined}>
      <div className={s.sessionHeader}><span>对话</span><button className={s.iconButton} aria-label="新副对话" onClick={newChat}><Plus size={20} /></button></div>
      <div className={s.sessionRows}>{state?.sessions.map(row => <div key={row.id} className={s.sessionRow + (row.id === sessionId ? ' ' + s.sessionSelected : '')}>
        <button className={s.sessionTitle} onClick={() => chooseSession(row)}><span title={row.title}>{row.title}</span><small>{row.kind === 'main' ? '日常空间' : time(row.updated)}{Boolean(row.unread) && ' · 未读 ' + row.unread}</small></button>
        {row.kind !== 'main' && <button className={s.sessionMore} aria-label={'管理对话 ' + row.title} aria-expanded={menuId === row.id} onClick={event => { menuTrigger.current = event.currentTarget; setMenuId(menuId === row.id ? '' : row.id) }}><MoreHorizontal size={18} /></button>}
        {menuId === row.id && <div className={s.sessionMenu} aria-label={'对话操作 ' + row.title}><button onClick={() => { edit('session', row); setMenuId('') }}>重命名</button><button onClick={() => void act(async () => { await api('/sessions/' + row.id, 'PATCH', { operation: 'archive' }); setMenuId(''); if (row.id === sessionId) chooseSession(state!.sessions.find(r => r.id === state!.main_session_id)!) }, '话题已封存，期间提醒转发主对话。')}>封存</button><button className={s.dangerText} onClick={() => void removeSession(row)}>删除</button></div>}
      </div>)}</div>
      <button className={s.textButton} onClick={() => navigate('search')}><Search size={16} />搜索话题</button><p className={s.sessionFoot}>保存在你的本机</p>
    </aside>}
    {paired && <div className={s.pairedChat + (pairedView === 'chat' ? ' ' + s.compactChatVisible : '')} inert={assistant || overlayOpen || navOpen || undefined}><button className={s.compactSwitch + ' ' + s.textButton} onClick={() => setPairedView('content')}>切换到{feedPaired ? '动态' : '目标'}</button>{chatPanel}</div>}
    <main id="main-content" tabIndex={-1} className={s.main + (paired ? ' ' + s.pairedGoals + (pairedView === 'chat' ? ' ' + s.compactGoalsHidden : '') : '')} inert={assistant || overlayOpen || navOpen || undefined}>
{page !== 'chat' && <header className={s.pageTopbar + ' ' + s.goalPanelControl}><button className={s.mobileMenu + ' ' + s.iconButton} aria-label="打开导航" onClick={() => setNavOpen(true)}><Menu size={22} /></button>{['goal','feed'].includes(page) && <button className={s.iconButton} aria-label={paired ? '收起并排聊天' : '打开并排聊天'} aria-expanded={paired} disabled={page === 'goal' && !resumableGoal} title={page === 'goal' && !resumableGoal ? '先开始一个目标引导，再打开聊天' : undefined} onClick={page === 'feed' ? toggleFeedChat : toggleGoalChat}><MessageCircle size={20} /></button>}</header>}

      {error && !modalOpen && !paired && <div role="alert" className={s.error}>{error}<button className={s.iconButton} aria-label="关闭错误提示" onClick={() => setError('')}><X size={16} /></button></div>}
      {notice && !paired && <div role="status" className={s.notice}>{notice}</div>}
      {!connected && <div role="status" className={s.connection}>正在连接本机服务。双击 start.command 可启动 Navi。</div>}
      {paired && <button className={s.compactSwitch + ' ' + s.textButton} onClick={() => setPairedView('chat')}>切换到当前聊天</button>}
      {page === 'chat' ? chatPanel : page === 'feed' && !detail ? null : <section ref={contentRef} className={s.pageContent} onScroll={rememberPosition}>
        {detail ? <><button className={s.textButton} onClick={() => setDetail(null)}><ChevronLeft size={17} />返回{title}</button><h1>{detail.payload.title}</h1><div className={s.meta}>{time(detail.created)} · {detail.kind === 'artifact' ? detail.payload.filename ? '已导出文件' : '草稿，可下载当前内容' : detail.kind === 'goal' ? labels[detail.payload.state || 'active'] : '已保存内容'}</div><Markdown>{detail.payload.body || ''}</Markdown>{detail.kind === "feed" && <div className={s.meta}>{detail.payload.sources?.map(source => <span key={source.id}>{source.url ? <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a> : source.title}{source.source_time ? " · " + time(source.source_time) : source.published_at ? " · " + source.published_at : ""}</span>)}</div>}<div className={s.rowActions}>{detail.kind !== 'artifact' && <button className={s.secondary} onClick={() => discuss(detail)}>带引用讨论</button>}{detail.kind !== 'artifact' && (detail.source_session ? <button className={s.textButton} onClick={() => chooseSession(detail.source_session!)}>回原话题：{detail.source_session.title}</button> : <span className={s.subtle}>暂无关联对话，将开启带引用的新讨论。</span>)}{detail.kind === 'artifact' && <a className={s.primary} href={'/api/artifacts/' + detail.id + '/download'}><Download size={17} />下载{detail.payload.filename ? '文件' : '草稿'}</a>}</div>
          {detail.kind === 'goal' && <><h2>下一步</h2><p>{detail.payload.next_step || '待明确'}</p><div className={s.rowActions}><button className={s.secondary} onClick={() => edit('goal', detail)}>编辑目标</button><button className={s.textButton} onClick={() => actPath('/records/' + detail.id, 'PUT', { ...detail.payload, state: detail.payload.state === 'paused' ? 'active' : 'paused' }, '目标状态已更新，提醒排程保持原有安排。')}>{detail.payload.state === 'paused' ? '恢复' : '暂停'}</button><button className={s.textButton} onClick={() => actPath('/records/' + detail.id, 'PUT', { ...detail.payload, state: 'completed' }, '目标及关联追踪已结束。')}>完成目标</button></div><h2>实际关联任务</h2>{detail.schedules.map(row => <p key={row.id}>{row.title} · {labels[row.state]}</p>)}{!detail.schedules.length && <p className={s.subtle}>暂无关联任务。</p>}<h2>相关成果</h2>{detail.artifacts.map(row => <button className={s.textButton} key={row.id} onClick={() => void openRecord(row)}>{row.payload.title}</button>)}{!detail.artifacts.length && <p className={s.subtle}>暂无关联成果。</p>}<h2>活动时间轴</h2>{detail.timeline.filter(row => eventLabels[row.kind]).map(row => <p key={row.id}>{time(row.created)} · {eventLabels[row.kind]}{row.data.next_step ? '：' + String(row.data.next_step) : ''}</p>)}</>}
        </> : <>
          {contentHeader}
          {page === 'goal' && <>
            {!!(trackingSchedules.length || trackingJobs.length) && <><h2>追踪</h2>{trackingSchedules.map(row => <div className={s.goalRow} key={row.id}><input type="checkbox" aria-label={'完成追踪 ' + row.title} disabled={working} checked={false} onChange={() => actPath('/schedules/' + row.id,'PATCH',{operation:'complete'},'追踪已结束')} /><span>{row.title}{row.state === 'paused' && <small> · 已暂停</small>}</span></div>)}{trackingJobs.map(row => <div className={s.goalRow} key={row.id}><input type="checkbox" aria-label={'结束工作 ' + row.input.slice(0,40)} disabled={working} checked={false} onChange={() => actPath('/jobs/' + row.id + '/cancel','POST',{},'工作已取消')} /><span>{row.input.slice(0,60)}</span></div>)}</>}
            {!!records.length && <><h2>目标</h2>{records.map(row => <div className={s.goalRow + (completedRows[row.id] ? ' ' + s.goalCompleted : '')} key={row.id} data-goal-id={row.id}><input type="checkbox" checked={Boolean(completedRows[row.id])} disabled={working || Boolean(completedRows[row.id])} aria-label={'完成目标 ' + row.payload.title} onChange={() => void completeGoal(row)} /><button className={s.goalAbstract} onClick={() => void openRecord(row)}>{String(row.payload.abstract || row.payload.title).slice(0,60)}{row.payload.state === 'paused' && <small> · 已暂停</small>}</button></div>)}</>}
          </>}
          {['idea', 'artifact'].includes(page) && <div className={s.rows}>{records.map(row => <article className={s.row} key={row.id}><div className={s.rowMain}><button className={s.rowTitle} onClick={() => void openRecord(row)}>{row.payload.title}</button><p>{page === 'idea' ? String(row.payload.abstract || row.payload.body?.slice(0,100) || '') : row.payload.body?.slice(0,220)}</p>{page === 'artifact' && <><div className={s.meta}><span>{time(row.created)}</span><span>{row.payload.filename ? '已导出文件' : '草稿'}</span></div><div className={s.rowActions}><a className={s.textButton} href={'/api/artifacts/' + row.id + '/download'}><Download size={16} />下载</a></div></>}</div></article>)}{!records.length && <p className={s.empty}>{page === 'idea' ? '还没有构想。每日更新成功后会显示五条，失败时保留上一批。' : '还没有成果。生成的草稿和审批后的文件会保存在这里。'}</p>}</div>}
          {page === 'goal' && <><h2>设定目标</h2><div className={s.goalCategories}>{[{id:'health',label:'健康',icon:Heart},{id:'relationships',label:'人际关系',icon:Users},{id:'finance',label:'财务',icon:Wallet},{id:'career',label:'职涯',icon:Briefcase},{id:'interests',label:'兴趣',icon:Palette},{id:'productivity',label:'生产力',icon:Zap},{id:'other',label:'其他',icon:Flag}].map(({id,label,icon:Icon}) => <button className={s.categoryRow} disabled={working} key={id} onClick={() => { capture(); setError(''); setGoalCategory({ id, label }) }}><Icon size={23} /><span>{label}</span><ChevronRight size={20} /></button>)}</div></>}
          {page === 'settings' && config && <form className={s.settings} onSubmit={event => { event.preventDefault(); void act(async () => { const { api_key_configured: _configured, ...fields } = config; const next = await api<Config>('/settings', 'PUT', { ...fields, ...(apiKey ? { api_key: apiKey } : {}) }); setConfig(next); setApiKey('') }, '设置已保存。连接是否可用须看真实检查结果。') }}>
            <h2>DeepSeek 连接</h2><p className={s.subtle}>{state?.settings.api_key_configured ? '密钥已保存在本机，不回显。' : '在本机输入密钥后进行真实检查。'}</p><label>API 密钥<input type="password" autoComplete="new-password" placeholder="留空保留现有密钥" value={apiKey} onChange={event => setApiKey(event.target.value)} /></label><div className={s.rowActions}><button type="submit" className={s.primary} disabled={working}>保存设置</button><button type="button" className={s.secondary} disabled={working || !!connectionJob || !state?.settings.api_key_configured} onClick={() => void act(async () => { const job = await api<Job>('/settings/check', 'POST', {}); setConnectionJob(job.id); setNotice('正在进行真实检查') })}>真实连接检查</button></div>
            <h2>提醒与授权</h2><label>普通站内提醒<select value={config.notification_policy} onChange={event => setConfig({ ...config, notification_policy: event.target.value })}><option value="daily">日常：按已有授权自动发送</option><option value="strict">严格：每条提醒人工审批</option></select></label><p className={s.subtle}>导出文件始终先审批。未批准内容不会进入 Outbox。</p><label>时区<input value={config.timezone} onChange={event => setConfig({ ...config, timezone: event.target.value })} /></label>
            <h2>隐私与运行</h2><p>模型调用会将本次有效会话、摘要、记忆、目标和引用资料发给 DeepSeek。停用记忆影响以后的调用。</p><p>双击 start.command 启动，启动终端与浏览器可以关闭。真正停止使用 stop.command。电脑休眠期间无法实时执行，恢复后每个调度最多补做一次。</p>
            {state && <><h2>今日使用</h2><p>{state.usage.requests} 次请求 · {state.usage.input_tokens + state.usage.output_tokens} tokens</p><p className={s.subtle}>{state.usage.cost_configured ? '按当前费率粗估 ' + state.usage.estimated_cost.toFixed(4) + ' ' + state.usage.currency : '尚未配置费率，以服务商账单为准。'}</p></>}
            <details className={s.advanced}><summary>预算与调用限制</summary><p className={s.subtle}>每日上限设为 0 时不设置本地日累计上限；每个作业的调用次数和执行期限仍有限制。</p><div className={s.fieldPair}>{[['request_timeout', '请求超时，秒'], ['job_timeout', '作业期限，秒'], ['max_model_calls', '单作业模型调用次数'], ['max_tool_calls', '单作业工具调用次数'], ['max_output_tokens', '输出 token 上限'], ['daily_requests', '每日请求上限'], ['daily_tokens', '每日 token 上限'], ['input_price_per_million', '每百万输入 token 费率'], ['output_price_per_million', '每百万输出 token 费率']].map(([key, label]) => <label key={key}>{label}<input type="number" min={key.includes('price') || key.startsWith('daily_') ? 0 : 1} step="any" value={Number(config[key])} onChange={event => setConfig({ ...config, [key]: Number(event.target.value) })} /></label>)}</div></details>
            <details className={s.advanced}><summary>高级设置与运行记录</summary><label>模型<input value={config.model} onChange={event => setConfig({ ...config, model: event.target.value })} /></label><label>接口协议<select value={config.protocol} onChange={event => setConfig({ ...config, protocol: event.target.value })}><option value="messages">Messages</option><option value="chat_completions">Chat Completions</option></select></label><label>简报工作流<select value={config.workflow} onChange={event => setConfig({ ...config, workflow: event.target.value })}><option value="priority_briefing">重要事项</option><option value="deadline_briefing">临近截止事项</option></select></label><label>费用货币<input value={String(config.price_currency)} onChange={event => setConfig({ ...config, price_currency: event.target.value })} /></label><button type="button" className={s.textButton} onClick={() => void history('/audit')}>完整运行记录</button><button type="button" className={s.textButton} disabled={!sessionId || !!busy} onClick={() => { navigate('chat'); saveDraft('请压缩当前会话，保留目标和未完成工作', references, 'compact'); setNotice('发送后将调用模型压缩，原始历史仍保留。') }}>准备手动压缩当前对话</button></details>
          </form>}
        </>}
      </section>}
      {state && <FeedPanel snapshot={state.records.filter(row => row.kind === 'feed' && row.payload.origin === 'generated')} header={contentHeader} active={page === 'feed' && !detail} layoutKey={String(feedChatVisible) + pairedView} scrollRef={feedRef} initialTop={positions.current.feed || 0} position={top => { positions.current.feed = top; localStorage.setItem('navi-reading-positions', JSON.stringify(positions.current)) }} discuss={discussFeed} detail={row => void openRecord(row)} showImage={(image, node) => { feedImageTrigger.current = node; setFeedImage(image) }} />}
    </main>
    {feedImage && <Dialog title="文章配图" trigger={feedImageTrigger.current} close={() => setFeedImage(null)}><img className={s.feedLargeImage} src={'/api/feed-media/' + feedImage.asset_id} alt={feedImage.alt} /><p className={s.subtle}>{feedImage.alt}</p><a href={feedImage.source_url} target="_blank" rel="noreferrer">图片原始来源</a></Dialog>}
    {goalCategory && <Dialog title={'设定' + goalCategory.label + '目标'} trigger={trigger.current} close={() => { if (!goalStarting.current) setGoalCategory(null) }}><p>我们会通过短对话一起确定目标和下一步，保存后在目标页跟进。</p>{error && <p role="alert" className={s.failureText}>{error}</p>}<button className={s.primary} disabled={working} onClick={() => void startGoal(goalCategory.id)}>{working ? '正在开始' : '现在就开始'}</button></Dialog>}
    {completedOpen && <Dialog title="已完成目标" trigger={trigger.current} close={() => { setCompletedOpen(false); setMenuId('') }}>{state?.records.filter(row => row.kind === 'goal' && row.payload.state === 'completed').map(row => <div className={s.goalRow} key={row.id}><input type="checkbox" checked disabled={working} aria-label={'恢复目标 ' + row.payload.title} onChange={() => actPath('/goals/' + row.id + '/state','POST',{operation:'reactivate'},'目标已恢复，原提醒保持结束')} /><span>{String(row.payload.abstract || row.payload.title)}</span><div className={s.goalMore}><button className={s.iconButton} aria-label={'管理已完成目标 ' + row.payload.title} aria-expanded={menuId === row.id} onClick={event => { menuTrigger.current = event.currentTarget; setMenuId(menuId === row.id ? '' : row.id) }}><MoreHorizontal size={18} /></button>{menuId === row.id && <div className={s.optionsMenu}><button className={s.dangerText} onClick={() => { removeItem(row); setCompletedOpen(false); setMenuId('') }}>删除</button></div>}</div></div>)}{!state?.records.some(row => row.kind === 'goal' && row.payload.state === 'completed') && <p>暂无已完成目标。</p>}<p className={s.subtle}>恢复目标后，已结束的提醒保持结束；需要时重新设置。</p></Dialog>}
    {idea && <IdeaDialog row={idea} trigger={trigger.current} close={() => setIdea(null)} started={id => void openSession(id)} stale={() => void refresh()} />}
    {sourcesOpen && state && <SourcesDialog state={state} trigger={trigger.current} close={() => setSourcesOpen(false)} refresh={refresh} />}
    {searchOpen && <SearchDialog trigger={searchTrigger.current} close={() => { setSearchOpen(false); if (location.hash === '#search') location.hash = 'chat' }} open={chooseSearch} recent={recentSearch} cache={setRecentSearch} />}
    {assistant && state && <div inert={overlayOpen || undefined}><Dialog drawer title="关于 Navi" trigger={assistantTrigger.current} close={() => setAssistant(false)}><AssistantDetails state={state} tab={tab} memory={memory} memoryId={memoryId} setTab={setTab} setMemory={setMemory} working={working} edit={edit} act={actPath} remove={removeItem} openSession={id => void openSession(id)} openRecord={row => void openRecord(row)} decide={decide} history={path => void history(path)} /></Dialog></div>}
    {modalOpen && <Dialog title={deletion ? deletion.item?.kind === 'goal' ? '删除目标' : '确认永久删除' : editor ? '编辑' + ({ memory: '记忆', goal: '目标', task: '提醒', check: '关注事项', action: '审核内容', source: '来源', session: '对话标题', brief: '关注说明' })[editor.kind] : '运行记录'} trigger={trigger.current} close={closeModal} cancelFirst={Boolean(deletion)}>
      {error && <p className={s.failureText} role="alert">{error}</p>}
      {deletion && <><p>{deletion.item?.kind === 'goal' ? '将目标从目标列表移除，相关对话、文件和审计保留。' : deletion.session ? '此对话删除后会永久删除，且无法复原。' : '此条目删除后会永久删除，且无法复原。'}</p>{deletion.session && <><p>消息和摘要一并删除。独立保存的记忆、目标、成果及必要执行记录继续保留。</p>{Boolean(deletion.reminders?.length) && <><p>此对话关联的未完成提醒也会一并取消：</p>{deletion.reminders?.map(row => <p key={row.id}>{row.title} · {labels[row.kind]}</p>)}</>}<p className={s.subtle}>可停止的关联工作会停止。正在完成的文件写入结束后再删除，不会转发未完成提醒。</p></>}{working && <p role="status">正在等待关联工作停止或结束，尚未完成删除。</p>}<div className={s.settingsActions}><button data-cancel className={s.secondary} disabled={working} onClick={closeModal}>取消</button><button className={s.danger} disabled={working} onClick={() => void act(async () => { if (deletion.session) { await api('/sessions/' + deletion.session.id, 'DELETE', {}); localStorage.removeItem('navi-draft-' + deletion.session.id); if (sessionId === deletion.session.id) chooseSession(state!.sessions.find(row => row.id === state!.main_session_id)!) } else if (deletion.check) await api('/check-items', 'PUT', state!.check_items.filter(row => row.id !== deletion.check!.id)); else await api('/records/' + deletion.item!.id, 'DELETE', {}); setDeletion(null) }, deletion.item?.kind === 'goal' ? '已移除目标，相关对话、文件和审计保留' : '已永久删除')}>删除</button></div></>}
      {editor && <form onSubmit={saveEditor}>
        {!['source', 'brief'].includes(editor.kind) && <label>标题<input data-first required maxLength={editor.kind === 'session' ? 100 : 300} value={editor.values.title || ''} onChange={event => setEditor({ ...editor, values: { ...editor.values, title: event.target.value } })} /></label>}
        {editor.kind === 'source' && <><label>英文官方来源<select data-first value={editor.values.source_id || state?.feed_catalog[0]?.id} onChange={event => setEditor({ ...editor, values: { ...editor.values, source_id: event.target.value } })}>{state?.feed_catalog.map(row => <option key={row.id} value={row.id}>{row.title}</option>)}</select></label><p className={s.subtle}>读取公开 RSS。关注来源与生成 Feed 均不会自动安排聊天提醒。</p></>}
        {!['source', 'session'].includes(editor.kind) && <label>{editor.kind === 'brief' ? '以后想持续了解什么' : editor.kind === 'task' ? '到时提醒什么' : '内容'}<textarea data-first={editor.kind === 'brief' || undefined} rows={5} required={editor.kind === 'task' || editor.kind === 'action'} maxLength={editor.kind === 'brief' ? 2000 : 20000} value={editor.values[editor.kind === 'task' ? 'instruction' : 'body'] || ''} onChange={event => setEditor({ ...editor, values: { ...editor.values, [editor.kind === 'task' ? 'instruction' : 'body']: event.target.value } })} /></label>}
        {editor.kind === 'brief' && <p className={s.subtle}>只影响以后生成的内容，旧帖子保持原样。</p>}
        {editor.kind === 'goal' && <><label>下一步<input value={editor.values.next_step || ''} onChange={event => setEditor({ ...editor, values: { ...editor.values, next_step: event.target.value } })} /></label><label>状态<select value={editor.values.state} onChange={event => setEditor({ ...editor, values: { ...editor.values, state: event.target.value } })}><option value="active">进行中</option><option value="paused">已暂停</option><option value="completed">已完成</option></select></label><p className={s.subtle}>完成目标会结束精确关联的追踪与提醒。</p></>}
        {editor.kind === 'task' && <>{!editor.id && <label>提醒方式<select value={editor.values.kind} onChange={event => setEditor({ ...editor, values: { ...editor.values, kind: event.target.value } })}><option value="once">单次</option><option value="recurring">周期</option><option value="heartbeat">自动检查</option></select></label>}<div className={s.fieldPair}>{editor.values.kind !== 'once' && <label>间隔，分钟<input type="number" min={1 / 60} step="any" required value={editor.values.minutes} onChange={event => setEditor({ ...editor, values: { ...editor.values, minutes: event.target.value } })} /></label>}<label>首次或下次时间<input type="datetime-local" required value={editor.values.next_due} onChange={event => setEditor({ ...editor, values: { ...editor.values, next_due: event.target.value } })} /></label></div>{!editor.id && <><p>归属：主对话</p><label>关联目标，可留空<select value={editor.values.goal_id} onChange={event => setEditor({ ...editor, values: { ...editor.values, goal_id: event.target.value } })}><option value="">不关联</option>{state?.records.filter(row => row.kind === 'goal').map(row => <option key={row.id} value={row.id}>{row.payload.title}</option>)}</select></label></>}</>}
        {editor.kind === 'check' && <><label>重要程度<input type="number" min={1} max={3} required value={editor.values.priority} onChange={event => setEditor({ ...editor, values: { ...editor.values, priority: event.target.value } })} /></label><label>截止时间，可留空<input type="datetime-local" value={editor.values.due_at} onChange={event => setEditor({ ...editor, values: { ...editor.values, due_at: event.target.value } })} /></label><label>来源链接，可留空<input type="url" value={editor.values.source_url || ''} onChange={event => setEditor({ ...editor, values: { ...editor.values, source_url: event.target.value } })} /></label><p className={s.subtle}>此日期不会自动安排提醒。</p></>}
        {editor.kind === 'action' && <label>执行目标<input required value={editor.values.target} onChange={event => setEditor({ ...editor, values: { ...editor.values, target: event.target.value } })} /></label>}
        <div className={s.settingsActions}><button className={s.primary} type="submit" disabled={working}>保存{editor.kind === 'action' ? '并重新审批' : ''}</button><button className={s.secondary} type="button" onClick={closeModal}>取消</button></div>
      </form>}
      {Boolean(audit) && <pre>{JSON.stringify(audit, null, 2)}</pre>}
    </Dialog>}
  </div>
}
