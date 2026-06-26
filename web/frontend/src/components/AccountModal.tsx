import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { addMt5Account } from '../api'

interface Props {
  open: boolean
  onClose: () => void
  onAdded: (id: number) => void
}

// Add-MT5-account popup. Styled after IndicatorsModal (tests-page header): a
// dark centered panel over a dimmed backdrop, Esc to close.
export default function AccountModal({ open, onClose, onAdded }: Props) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [server, setServer] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // Reset the form each time the modal opens.
  useEffect(() => {
    if (open) { setName(''); setLogin(''); setPassword(''); setServer(''); setBusy(false) }
  }, [open])

  if (!open) return null

  async function handleSave() {
    if (!login.trim() || busy) return
    setBusy(true)
    try {
      const id = await addMt5Account({ name: name.trim(), login: login.trim(), password, server: server.trim() })
      await queryClient.invalidateQueries({ queryKey: ['mt5Accounts'] })
      onAdded(id)
      onClose()
    } catch (e) {
      console.error('Failed to add MT5 account:', e)
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative z-10 w-[500px] bg-[#222831] backdrop-blur rounded-lg shadow-2xl flex flex-col">
        <div className="px-4 py-3 flex items-center justify-between">
          <span className="text-xs font-semibold tracking-widest uppercase text-gray-500">Add MT5 Account</span>
          <button onClick={onClose} className="text-gray-600 hover:text-gray-300 transition-colors p-0.5">
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <line x1="1" y1="1" x2="11" y2="11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              <line x1="11" y1="1" x2="1" y2="11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        <div className="px-4 py-3 flex flex-col gap-3">
          <ModalField label="Name" value={name} onChange={setName} placeholder="My account" />
          <ModalField label="Login" value={login} onChange={setLogin} placeholder="12345678" />
          <ModalField label="Password" value={password} onChange={setPassword} placeholder="••••••" type="password" />
          <ModalField label="Server" value={server} onChange={setServer} placeholder="Broker-Server" />
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
            disabled={!login.trim() || busy}
            className={`px-4 py-1.5 text-xs font-medium rounded-md transition-colors ${
              login.trim() && !busy
                ? 'bg-emerald-600 text-white hover:bg-emerald-500'
                : 'bg-gray-700/60 text-gray-500 cursor-default'
            }`}
          >
            {busy ? 'Saving…' : 'Add account'}
          </button>
        </div>
      </div>
    </div>
  )
}

function ModalField({
  label, value, onChange, placeholder, type = 'text',
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  type?: string
}) {
  return (
    <div className="flex items-center gap-3">
      <label className="w-20 text-xs text-gray-500 shrink-0">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="flex-1 bg-black/20 border border-white/10 text-sm text-gray-200 rounded-md px-3 py-1.5 outline-none focus:border-white/25 transition-colors placeholder:text-gray-600"
      />
    </div>
  )
}
