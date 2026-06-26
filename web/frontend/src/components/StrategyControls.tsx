import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchAccountStrategies, deleteAccountStrategy, setAccountStrategyActive } from '../api'
import { useApp } from '../context/AppContext'

// March-header strategy bar for the selected account: a "+" button that opens
// the add-strategy modal, followed by a horizontal list of the account's
// strategies — each with an on/off toggle and a trash icon.
export default function StrategyControls() {
  const { selectedAccountId, setMarchStrategyModalOpen } = useApp()
  const queryClient = useQueryClient()

  const { data: strategies } = useQuery({
    queryKey: ['accountStrategies', selectedAccountId],
    queryFn: () => fetchAccountStrategies(selectedAccountId as number),
    enabled: selectedAccountId != null,
  })

  if (selectedAccountId == null) return null

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ['accountStrategies', selectedAccountId] })
  }

  async function handleToggle(id: number, active: boolean) {
    try {
      await setAccountStrategyActive(selectedAccountId as number, id, !active)
      await refresh()
    } catch (e) {
      console.error('Failed to toggle strategy:', e)
    }
  }

  async function handleDelete(id: number) {
    try {
      await deleteAccountStrategy(selectedAccountId as number, id)
      await refresh()
    } catch (e) {
      console.error('Failed to delete strategy:', e)
    }
  }

  return (
    <div className="flex items-center gap-2 min-w-0">
      {/* Add-strategy button */}
      <button
        onClick={() => setMarchStrategyModalOpen(true)}
        title="Add strategy"
        className="flex items-center justify-center w-7 h-7 shrink-0 rounded-lg bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/25 transition-colors text-base leading-none"
      >
        +
      </button>

      {/* Horizontal strategy list */}
      <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar">
        {strategies?.map(s => (
          <div
            key={s.id}
            className="flex items-center gap-1.5 pl-2 pr-1 py-1 rounded-lg bg-gray-900 border border-gray-800/80 shrink-0"
          >
            <span className="text-xs font-medium text-gray-200">{s.strategy}</span>
            {s.symbol ? <span className="text-[10px] text-gray-500">{s.symbol}</span> : null}

            {/* On/off toggle */}
            <button
              onClick={() => handleToggle(s.id, s.active)}
              title={s.active ? 'Live — click to turn off' : 'Off — click to turn on'}
              className={`flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium rounded transition-colors ${
                s.active
                  ? 'bg-emerald-500/15 text-emerald-400 hover:bg-emerald-500/25'
                  : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800/70'
              }`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${s.active ? 'bg-emerald-400' : 'bg-gray-600'}`} />
              {s.active ? 'On' : 'Off'}
            </button>

            {/* Trash */}
            <button
              onClick={() => handleDelete(s.id)}
              title="Remove strategy"
              className="text-gray-600 hover:text-red-400 transition-colors p-0.5"
            >
              <TrashIcon />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

function TrashIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  )
}
