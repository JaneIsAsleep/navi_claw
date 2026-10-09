import { useEffect, useRef, type ReactNode, type MutableRefObject } from 'react'
import { X } from 'lucide-react'
import s from './App.module.css'

// The caller captures the trigger before changing state, including pointer input.
export type CloseReason = 'pointer' | 'keyboard' | 'selection'
export function Dialog({ title, trigger, close, children, cancelFirst = false, drawer = false, wide = false, focusReturnMode = 'default', closeReason }: { title: string; trigger: HTMLElement | null; close: () => void; children: ReactNode; cancelFirst?: boolean; drawer?: boolean; wide?: boolean; focusReturnMode?: 'default' | 'search'; closeReason?: MutableRefObject<CloseReason> }) {
  const ref = useRef<HTMLDivElement>(null)
  const internalReason = useRef<CloseReason>('keyboard')
  const reason = closeReason || internalReason
  const onClose = useRef(close)
  onClose.current = close
  useEffect(() => {
    const priorOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const nodes = () => Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], summary, [tabindex="0"]') || []).filter(node => node.getClientRects().length > 0)
    const first = cancelFirst ? ref.current?.querySelector<HTMLElement>('[data-cancel]') : ref.current?.querySelector<HTMLElement>('[data-first]') || nodes()[0]
    first?.focus()
    const key = (event: KeyboardEvent) => {
      reason.current = 'keyboard'
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose.current() }
      if (event.key === 'Tab') {
        const list = nodes(), first = list[0], last = list[list.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
      }
    }
    ref.current?.addEventListener('keydown', key)
    const current = ref.current
    return () => { document.body.style.overflow = priorOverflow; current?.removeEventListener('keydown', key); requestAnimationFrame(() => {
      const destination = focusReturnMode === 'search' && reason.current === 'selection' ? document.getElementById('main-content') : trigger?.isConnected && trigger.getClientRects().length ? trigger : document.querySelector<HTMLElement>('[role="dialog"] button') || document.getElementById('main-content')
      if (focusReturnMode === 'search' && reason.current === 'pointer' && destination === trigger && trigger) {
        trigger.dataset.pointerReturn = 'true'
        const clear = () => { delete trigger.dataset.pointerReturn; trigger.removeEventListener('keydown', clear); trigger.removeEventListener('blur', clear) }
        trigger.addEventListener('keydown', clear); trigger.addEventListener('blur', clear)
      }
      destination?.focus({ preventScroll: true })
    }) }
  }, [trigger, cancelFirst])
  return <div className={s.modalBackdrop} onMouseDown={event => { if (event.target === event.currentTarget) { reason.current = 'pointer'; close() } }}>
    <div ref={ref} role="dialog" aria-modal="true" aria-label={title} className={drawer ? s.drawer : s.modal + (wide ? ' ' + s.wideModal : '')}>
      <div className={s.dialogHeader}><h2>{title}</h2><button className={s.iconButton} aria-label={drawer ? '返回原页面' : '关闭弹窗'} onClick={event => { reason.current = event.detail ? 'pointer' : 'keyboard'; close() }}><X size={20} /></button></div>
      {children}
    </div>
  </div>
}
