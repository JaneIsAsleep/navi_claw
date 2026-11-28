import { useEffect, useState } from 'react'
import { Bell, CalendarClock, ExternalLink, FileText, LogIn, LogOut, RefreshCw } from 'lucide-react'
import { api } from './data'
import type { CampusBrowserLogin, CampusDashboard, CampusDashboardItem, CampusDeadline, CampusStatus } from './data'
import s from './App.module.css'


function dueDistance(timestamp: number) {
  const due = new Date(timestamp * 1000)
  const today = new Date()
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime()
  const target = new Date(due.getFullYear(), due.getMonth(), due.getDate()).getTime()
  const days = Math.round((target - start) / 86400000)
  if (days <= 0) return '今天'
  if (days === 1) return '明天'
  return `${days} 天后`
}

function deadlineDate(timestamp: number) {
  const value = new Date(timestamp * 1000)
  return { month: `${value.getMonth() + 1}月`, day: value.getDate() }
}


export function CampusPanel() {
  const [status, setStatus] = useState<CampusStatus | null>(null)
  const [dashboard, setDashboard] = useState<CampusDashboard | null>(null)
  const [browserLogin, setBrowserLogin] = useState<CampusBrowserLogin>({ status: 'idle', error: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function refreshStatus() {
    const current = await api<CampusStatus>('/campus/status')
    setStatus(current)
    return current
  }

  async function refreshDashboard(refresh = false) {
    setBusy(true)
    setError('')
    try {
      setDashboard(await api<CampusDashboard>(`/campus/dashboard?refresh=${refresh}`))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '校园信息读取失败')
      setDashboard(null)
      const current = await refreshStatus().catch(() => null)
      if (current && !current.logged_in) setStatus(current)
    } finally {
      setBusy(false)
    }
  }

  async function revalidateDashboard() {
    try {
      setDashboard(await api<CampusDashboard>('/campus/dashboard?refresh=false'))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '校园信息更新失败')
    }
  }

  useEffect(() => {
    void refreshStatus()
      .then(current => current.logged_in ? refreshDashboard() : undefined)
      .catch(reason => setError(reason instanceof Error ? reason.message : '校园平台状态读取失败'))
  }, [])

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible' && status?.logged_in) void revalidateDashboard()
    }, 5 * 60 * 1000)
    return () => window.clearInterval(timer)
  }, [status?.logged_in])

  useEffect(() => {
    if (!dashboard?.stale) return
    let attempts = 0
    const timer = window.setInterval(() => {
      attempts += 1
      void revalidateDashboard()
      if (attempts >= 8) window.clearInterval(timer)
    }, 5000)
    return () => window.clearInterval(timer)
  }, [dashboard?.stale, dashboard?.cached_at])

  useEffect(() => {
    if (browserLogin.status !== 'waiting') return
    let cancelled = false
    const timer = window.setInterval(() => {
      void api<CampusBrowserLogin>('/campus/browser-login/status')
        .then(async current => {
          if (cancelled) return
          setBrowserLogin(current)
          if (current.status === 'succeeded') {
            const next = await refreshStatus()
            setStatus(next)
            if (next.logged_in) await refreshDashboard(true)
          }
          if (current.status === 'failed') setError(current.error || '浏览器登录失败')
        })
        .catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : '浏览器登录状态读取失败') })
    }, 1000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [browserLogin.status])

  async function startBrowserLogin() {
    setBusy(true)
    setError('')
    try {
      setBrowserLogin(await api<CampusBrowserLogin>('/campus/browser-login', 'POST', {}))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法启动浏览器登录')
    } finally {
      setBusy(false)
    }
  }

  async function logout() {
    setBusy(true)
    setError('')
    try {
      setStatus(await api<CampusStatus>('/campus/logout', 'POST', {}))
      setDashboard(null)
      setBrowserLogin({ status: 'idle', phase: '', error: '' })
    } finally {
      setBusy(false)
    }
  }

  function renderMessage(item: CampusDashboardItem) {
    return <article className={s.campusMessage} key={item.id}>
      <div className={s.campusMessageTop}>
        <time>{item.published_label}</time>
      </div>
      <h4>{item.title}</h4>
      {item.course && <p className={s.campusCourse}>{item.course}</p>}
      {item.summary && <p className={s.campusSummaryText}>{item.summary}</p>}
    </article>
  }

  function renderDeadline(item: CampusDeadline) {
    const date = deadlineDate(item.due_at)
    return <article className={s.campusDeadline} key={item.id}>
      <div className={s.campusDeadlineDate}><span>{date.month}</span><strong>{date.day}</strong></div>
      <div className={s.campusDeadlineBody}>
        <span className={s.campusDue}>{dueDistance(item.due_at)} · {item.due_label}</span>
        <h4>{item.title}</h4>
        <p className={s.campusCourse}>{item.course || item.source}</p>
        {item.summary && <p className={s.campusSummaryText}>{item.summary}</p>}
      </div>
    </article>
  }

  const browserLoginMessage = ({
    opening: '正在打开独立 Edge 登录窗口。',
    cas: '请在这个独立 Edge 窗口中完成 CAS 登录和人机验证。',
    validating: '登录成功，正在验证校园会话。',
    blackboard: 'TIS 已登录，正在自动连接 Blackboard。',
  } as Record<string, string>)[browserLogin.phase || ''] || '等待浏览器登录完成。'

  if (!status) return <p className={s.subtle}>正在读取校园平台状态。</p>

  if (!status.logged_in) {
    return <div className={s.settings}>
      <h2>CAS 登录</h2>
      <p className={s.subtle}>登录后只在本机保存平台会话，不保存 CAS 密码。校园看板会在本机整理 Blackboard 的通知、截止时间、作业和考试。</p>
      {error && <p role="alert" className={s.failureText}>{error}</p>}
      {browserLogin.status === 'waiting'
        ? <p role="status" className={s.subtle}>{browserLoginMessage} 成功后窗口会自动最小化。</p>
        : <button type="button" className={s.primary} disabled={busy} onClick={() => void startBrowserLogin()}><LogIn size={18} />使用独立浏览器登录</button>}
    </div>
  }

  return <div className={s.campusDashboard}>
    <header className={s.campusDashboardHeader}>
      <div>
        <span className={s.campusEyebrow}>BLACKBOARD</span>
        <h2>学习总览</h2>
        {dashboard?.brief && <p>{dashboard.brief}</p>}
      </div>
      <div className={s.rowActions}>
        {dashboard?.stale && <span className={s.badge}>{dashboard.refresh_error ? '缓存内容，更新失败' : '缓存内容，后台更新中'}</span>}
        <a className={s.secondary} href="https://bb.sustech.edu.cn/" target="_blank" rel="noopener noreferrer"><ExternalLink size={17} />打开 Blackboard</a>
        <button className={s.secondary} disabled={busy} onClick={() => void refreshDashboard(true)}><RefreshCw size={17} />刷新</button>
        <button className={s.textButton} disabled={busy} onClick={() => void logout()}><LogOut size={17} />退出 CAS</button>
      </div>
    </header>

    {error && <p role="alert" className={s.failureText}>{error}</p>}
    {!dashboard && busy && <p role="status" className={s.subtle}>正在整理校园信息。</p>}

    {dashboard && <>
      <div className={s.campusStats}>
        <div className={s.campusStat}><Bell size={19} /><span>近期消息</span><strong>{dashboard.summary.messages}</strong></div>
        <div className={s.campusStat}><CalendarClock size={19} /><span>临近截止</span><strong>{dashboard.summary.deadlines}</strong></div>
      </div>

      <section className={s.campusSection}>
        <div className={s.campusSectionHead}>
          <div><span className={s.campusEyebrow}>UP NEXT</span><h3>临近截止</h3></div>
        </div>
        {dashboard.deadlines.length
          ? <div className={s.campusDeadlineList}>{dashboard.deadlines.map(renderDeadline)}</div>
          : <p className={s.empty}>未来一段时间没有识别到截止事项。</p>}
      </section>

      <section className={s.campusSection}>
        <div className={s.campusSectionHead}>
          <div><span className={s.campusEyebrow}>RECENT</span><h3>近期消息</h3></div>
          <span className={s.subtle}>来自 Blackboard 公告</span>
        </div>
        {dashboard.recent_messages.length
          ? <div className={s.campusMessageGrid}>{dashboard.recent_messages.map(renderMessage)}</div>
          : <p className={s.empty}>近期没有新的课程公告。</p>}
      </section>

      <p className={s.campusUpdated}><FileText size={14} />信息由 Blackboard 页面自动整理{dashboard.cached_at ? `，上次更新 ${new Date(dashboard.cached_at * 1000).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}` : ''}，重要安排请打开原文核对。</p>
    </>}

  </div>
}
