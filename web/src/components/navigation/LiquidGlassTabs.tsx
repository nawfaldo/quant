import { useLayoutEffect, useRef, useState } from 'react'

interface Item {
  id: string
  label: string
}

interface Props {
  items: Item[]
  activeId: string
  onChange: (id: string) => void
  label: string
  blueActive?: boolean
}

interface Indicator {
  left: number
  width: number
}

export default function LiquidGlassTabs({ items, activeId, onChange, label, blueActive = false }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const tabRefs = useRef(new Map<string, HTMLButtonElement>())
  const previousActiveId = useRef(activeId)
  const [indicator, setIndicator] = useState<Indicator | null>(null)
  const [hasChangedSelection, setHasChangedSelection] = useState(false)
  const [animationNonce, setAnimationNonce] = useState(0)

  // Do not animate the restored selection when this component mounts after a
  // route change. The liquid effect is reserved for an actual tab change.
  useLayoutEffect(() => {
    if (previousActiveId.current === activeId) return
    previousActiveId.current = activeId
    setHasChangedSelection(true)
    setAnimationNonce((nonce) => nonce + 1)
  }, [activeId])

  useLayoutEffect(() => {
    const container = containerRef.current
    const activeTab = tabRefs.current.get(activeId)
    if (!container || !activeTab) return

    const measure = () => {
      const tab = tabRefs.current.get(activeId)
      if (!tab) return
      setIndicator({ left: tab.offsetLeft, width: tab.offsetWidth })
    }

    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(container)
    return () => observer.disconnect()
  }, [activeId, items])

  return (
    <div
      ref={containerRef}
      className={`liquid-glass-tabs${blueActive ? ' liquid-glass-tabs-blue-active' : ''}${hasChangedSelection ? ' liquid-glass-tabs-animated' : ''}`}
      role="tablist"
      aria-label={label}
    >
      <span
        className={`liquid-glass-tabs-indicator${indicator ? ' liquid-glass-tabs-indicator-ready' : ''}`}
        style={indicator ? { width: indicator.width, transform: `translate3d(${indicator.left}px, 0, 0)` } : undefined}
      >
        <span key={animationNonce} className="liquid-glass-tabs-indicator-surface" />
      </span>
      {items.map((item) => {
        const selected = item.id === activeId
        return (
          <button
            key={item.id}
            ref={(node) => {
              if (node) tabRefs.current.set(item.id, node)
              else tabRefs.current.delete(item.id)
            }}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(item.id)}
            className={`liquid-glass-tab ${selected ? 'liquid-glass-tab-active' : 'liquid-glass-tab-inactive'}`}
          >
            <span key={selected ? `${item.id}-${animationNonce}` : item.id} className="liquid-glass-tab-label">{item.label}</span>
          </button>
        )
      })}
    </div>
  )
}
