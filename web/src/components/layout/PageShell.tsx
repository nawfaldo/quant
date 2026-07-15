import type { ReactNode } from 'react'

interface PageShellProps {
  children: ReactNode
  minHeight?: boolean
}

export default function PageShell({ children, minHeight = false }: PageShellProps) {
  return (
    <div className={`flex flex-1 overflow-hidden${minHeight ? ' min-h-0' : ''}`}>
      {children}
    </div>
  )
}
