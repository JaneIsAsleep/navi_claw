export type Reference = { id: string; kind: string; title: string; body: string; source_url?: string; source_ids?: string[] }
export type FeedImage = { asset_id: string; source_url: string; alt: string; width?: number; height?: number }
export type Payload = { title: string; body?: string; enabled?: boolean; state?: string; next_step?: string; source?: string; source_url?: string; checked_at?: number; source_time?: number; source_ids?: string[]; icon_key?: string; images?: FeedImage[]; sources?: { id: string; title: string; url: string; published_at?: string; source_time?: number; read_scope?: string; read_at?: number }[]; filename?: string; job_id?: string; source_job_id?: string; session_id?: string; goal_ids?: string[]; basis?: { memories?: Payload[]; goals?: Payload[] }; [key: string]: unknown }
export type Item = { id: string; kind: string; payload: Payload; created: number; updated: number; source_session?: Session }
export type Session = { id: string; title: string; summary?: string; display_abstract: string; goal_setup_json?: string; kind: string; state: string; created: number; updated: number; unread?: number; match_kind?: string; match_message_id?: string; match_excerpt?: string }
export type Job = { id: string; session_id?: string; task_id?: string; kind: string; status: string; input: string; result: string; error: string; attempt: number; created: number }
export type Action = { id: string; job_id: string; kind: string; title: string; target: string; body: string; status: string; content_hash: string; basis: string; created: number; source_session_id?: string; source_title?: string; source_state?: string; version: number; delivery_state?: string; delivery_error?: string }
export type Schedule = { id: string; task_id: string; title: string; instruction: string; kind: string; state: string; interval_seconds: number | null; next_due: number; enabled: number; goal_id?: string; session_id?: string }
export type CheckItem = { id: string; title: string; body?: string; priority: number; done: boolean; due_at?: number | null; source_url?: string }
export type Config = { model: string; protocol: string; workflow: string; notification_policy: string; api_key_configured: boolean; timezone: string; feed_brief: string; [key: string]: string | number | boolean }
export type AutoContent = { latest: { id: string; status: string; error: string; error_code: string; next_run: number; recovery_count: number; updated: number; result: string } | null; last_success: number | null }
export type State = { auto_content: { feed: AutoContent; ideas: AutoContent; next_due: number; timezone: string }; sessions: Session[]; main_session_id: string; jobs: Job[]; records: Item[]; actions: Action[]; notifications: { id: string; title: string; body: string; unread: number; created: number }[]; schedules: Schedule[]; check_items: CheckItem[]; feed_brief: string; feed_catalog: { id: string; title: string; url: string; source_type?: 'builtin' | 'custom' }[]; settings: Config; usage: { requests: number; input_tokens: number; output_tokens: number; estimated: number; cost_configured: boolean; estimated_cost: number; currency: string } }
export type GoalCard = { type: 'goal_choices'; card_id: string; setup_version: number; question: string; choices: { id: string; label: string; value: string; operation: string }[]; selected?: string }
export type GoalSetup = { status?: string; category?: string; version?: number; active_card_id?: string; saved_goal_id?: string }
export type Message = { interaction?: GoalCard; id: string; role: string; content: string; job_id: string; created: number; references: Reference[]; outbox_id?: string; origin_session_id?: string; unread: number }
export type Detail = Item & { source_session?: Session; schedules: Schedule[]; artifacts: Item[]; timeline: { id: number; kind: string; created: number; data: Record<string, unknown> }[] }
export type Editor = { kind: 'memory' | 'goal' | 'task' | 'check' | 'action' | 'source' | 'session' | 'brief'; id?: string; values: Record<string, string> }
export type Tab = 'activity' | 'approvals' | 'reminders' | 'identity'
export const labels: Record<string, string> = { pending: '等待处理', running: '进行中', succeeded: '已生成', failed: '失败', cancelled: '已取消', timed_out: '已超时', approved: '已批准，等待执行', rejected: '已拒绝，未执行', delivered: '已投递', invalidated: '已失效，未执行', uncertain: '执行结果待核查', active: '进行中', paused: '已暂停', completed: '已完成', once: '单次提醒', recurring: '周期提醒', heartbeat: '自动检查' }
export const terminal = (status: string) => ['succeeded', 'failed', 'cancelled', 'timed_out'].includes(status)
export const time = (value: number) => new Date(value * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Taipei', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
export const localDate = (value: number) => { const date = new Date(value * 1000); return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16) }
export class APIError extends Error {
  constructor(message: string, public status: number) { super(message) }
}
export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch('/api' + path, { method, headers: { 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
  const value = await response.json()
  if (!response.ok) throw new APIError(value.detail || '请求失败，请稍后重试', response.status)
  return value
}
