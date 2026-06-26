import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchMt5Accounts, deleteMt5Account } from '../api'
import { useApp } from '../context/AppContext'

// Custom account dropdown for the March header. A native <select> can't hold a
// clickable delete icon per row, so this is a hand-rolled popdown: each account
// row has a trash icon on the right, plus an "+ Add account…" action at the end.
//
// The menu is rendered through a portal at <body> with fixed positioning. The
// header uses backdrop-blur, which creates a stacking context that would
// otherwise trap an absolutely-positioned menu *under* the z-50 fixed sidebar.
export default function AccountSelect() {
  const { selectedAccountId, setSelectedAccountId, setMarchAccountModalOpen } = useApp()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const { data: accounts } = useQuery({
    queryKey: ['mt5Accounts'],
    queryFn: fetchMt5Accounts,
  })

  function toggle() {
    if (open) { setOpen(false); return }
    const r = btnRef.current?.getBoundingClientRect()
    if (r) setPos({ top: r.bottom + 4, right: window.innerWidth - r.right })
    setOpen(true)
  }

  // Close on outside click (accounting for the portaled menu) and on Escape.
  useEffect(() => {
    if (!open) return
    function onDown(e: MouseEvent) {
      const t = e.target as Node
      if (btnRef.current?.contains(t) || menuRef.current?.contains(t)) return
      setOpen(false)
    }
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const selected = accounts?.find(a => a.id === selectedAccountId) ?? null
  const label = selected ? (selected.name || selected.login) : 'Select account'

  async function handleDelete(id: number, e: React.MouseEvent) {
    e.stopPropagation()
    try {
      await deleteMt5Account(id)
      await queryClient.invalidateQueries({ queryKey: ['mt5Accounts'] })
      // The header effect reselects another account (or clears the selection).
    } catch (err) {
      console.error('Failed to delete account:', err)
    }
  }

  return (
    <>
      <button
        ref={btnRef}
        onClick={toggle}
        title="MT5 account"
        className="flex items-center gap-1.5 shrink-0 bg-gray-900 border border-gray-800/80 text-xs font-medium text-gray-200 rounded-lg px-2.5 py-1.5 outline-none cursor-pointer hover:border-gray-700 transition-colors max-w-[200px]"
      >
        <span className="truncate">{label}</span>
        <svg
          width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
          className={`text-gray-500 transition-transform ${open ? 'rotate-180' : ''}`}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && pos && createPortal(
        <div
          ref={menuRef}
          style={{ position: 'fixed', top: pos.top, right: pos.right }}
          className="w-[230px] bg-gray-900 border border-gray-800 rounded-lg shadow-2xl z-[100] py-1 max-h-[320px] overflow-y-auto"
        >
          {accounts?.map(a => (
            <div
              key={a.id}
              onClick={() => { setSelectedAccountId(a.id); setOpen(false) }}
              className={`group flex items-center gap-2 px-3 py-2 cursor-pointer transition-colors ${
                a.id === selectedAccountId ? 'bg-gray-800/70' : 'hover:bg-gray-800/40'
              }`}
            >
              <div className="min-w-0 flex-1">
                <div className="text-xs text-gray-200 truncate">{a.name || a.login}</div>
                {a.name ? <div className="text-[10px] text-gray-500 truncate">{a.login}</div> : null}
              </div>
              <button
                onClick={(e) => handleDelete(a.id, e)}
                title="Delete account"
                className="text-gray-600 hover:text-red-400 transition-colors p-0.5 shrink-0"
              >
                <TrashIcon />
              </button>
            </div>
          ))}

          <div className="h-px bg-gray-800 my-1" />

          <div
            onClick={() => { setMarchAccountModalOpen(true); setOpen(false) }}
            className="px-3 py-2 text-xs font-medium text-emerald-400 hover:bg-gray-800/40 cursor-pointer transition-colors"
          >
            + Add account…
          </div>
        </div>,
        document.body,
      )}
    </>
  )
}

function TrashIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  )
}
