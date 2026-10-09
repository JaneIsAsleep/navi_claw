import ReactMarkdown from 'react-markdown'
import s from './App.module.css'

export function Markdown({ children, feed = false }: { children: string; feed?: boolean }) {
  return <div className={s.markdown}><ReactMarkdown skipHtml={feed} components={{ a: ({ href, children }) => feed ? <span>{children}</span> : <a href={href} target="_blank" rel="noreferrer">{children}</a>, ...(feed ? { img: () => null } : {}) }}>{children}</ReactMarkdown></div>
}
