import { type ReactNode } from 'react'
import { ChevronLeft, Maximize2 } from 'lucide-react'
import s from './App.module.css'

export function ChatPanel({ avatar, controls, children, split, toggle, returnLabel = '返回并排目标' }: { avatar: ReactNode; controls?: ReactNode; children: ReactNode; split: boolean; toggle?: () => void; returnLabel?: string }) {
  return <div className={s.chatPanel}>
    <header className={s.topbar}>{controls}<div className={s.chatAvatar}>{avatar}</div>{toggle && <button className={s.expandChat + ' ' + s.iconButton} aria-label={split ? '展开完整聊天' : returnLabel} onClick={toggle}>{split ? <Maximize2 size={18} /> : <ChevronLeft size={18} />}</button>}</header>
    {children}
  </div>
}
