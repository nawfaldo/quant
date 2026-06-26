import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { addAccountStrategy, KNOWN_MARCH_STRATEGIES } from '../api'

interface Props {
  open: boolean
  onClose: () => void
  accountId: number | null
}

// Add-strategy popup for the selected MT5 account. Styled after IndicatorsModal
// / AccountModal: a dark centered panel over a dimmed backdrop, Esc to close.
export default function StrategyModal({ open, onClose, accountId }: Props) {
  const queryClient = useQueryClient()
  const [strategy, setStrategy] = useState<string>(KNOWN_MARCH_STRATEGIES[0])
  const [symbol, setSymbol] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  useEffect(() => {
    if (open) { setStrategy(KNOWN_MARCH_STRATEGIES[0]); setSymbol(''); setBusy(false) }
  }, [open])

  if (!open) return null

  async function handleSave() {
    if (!symbol.trim() || accountId == null || busy) return
    setBusy(true)
    try {
      await addAccountStrategy(accountId, { strategy, symbol: symbol.trim() })
      await queryClient.invalidateQueries({ queryKey: ['accountStrategies', accountId] })
      onClose()
    } catch (e) {
      console.error('Failed to add strategy:', e)
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative z-10 w-[500px] bg-[#222831] backdrop-blur rounded-lg shadow-2xl flex flex-col">
        <div className="px-4 py-3 flex items-center justify-between">
          <span className="text-xs font-semibold tracking-widest uppercase text-gray-500">Add Strategy</span>
          <button onClick={onClose} className="text-gray-600 hover:text-gray-300 transition-colors p-0.5">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <line x1="1" y1="1" x2="11" y2="11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              <line x1="11" y1="1" x2="1" y2="11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        <div className="px-4 py-3 flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <label className="w-20 text-xs text-gray-500 shrink-0">Strategy</label>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              className="flex-1 bg-black/20 border border-white/10 text-sm text-gray-200 rounded-md px-3 py-1.5 outline-none focus:border-white/25 transition-colors cursor-pointer"
            >
              {KNOWN_MARCH_STRATEGIES.map(s => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-3">
            <label className="w-20 text-xs text-gray-500 shrink-0">Symbol</label>
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              placeholder="NAS100"
              className="flex-1 bg-black/20 border border-white/10 text-sm text-gray-200 rounded-md px-3 py-1.5 outline-none focus:border-white/25 transition-colors placeholder:text-gray-600"
            />
          </div>
        </div>

        <div className="px-4 py-3 flex items-center justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs font-medium rounded-md text-gray-400 hover:text-gray-200 hover:bg-white/5 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={!symbol.trim() || accountId == null || busy}
            className={`px-4 py-1.5 text-xs font-medium rounded-md transition-colors ${
              symbol.trim() && accountId != null && !busy
                ? 'bg-emerald-600 text-white hover:bg-emerald-500'
                : 'bg-gray-700/60 text-gray-500 cursor-default'
            }`}
          >
            {busy ? 'Saving…' : 'Add strategy'}
          </button>
        </div>
      </div>
    </div>
  )
}
