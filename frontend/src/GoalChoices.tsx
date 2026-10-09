import { type GoalCard } from './data'
import s from './App.module.css'
import { Check } from 'lucide-react'

export function GoalChoices({ card, active, working, choose }: { card: GoalCard; active: boolean; working: boolean; choose: (id: string) => void }) {
  return <div className={s.goalChoices} aria-label="目标选项">
    {card.choices.map(choice => <button className={choice.operation === 'save' ? s.primary : s.secondary} key={choice.id} disabled={!active || working || Boolean(card.selected)} aria-pressed={card.selected === choice.id} onClick={() => choose(choice.id)}>{card.selected === choice.id && <Check size={16} aria-hidden="true" />}{choice.label}</button>)}
    {!active && !card.selected && <span className={s.subtle}>此步骤已更新</span>}
  </div>
}
