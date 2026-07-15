import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { createEnvironment, fetchEnvironments } from '../api'
import PageShell from '../components/layout/PageShell'

export const Route = createFileRoute('/environment/')({
  component: EnvironmentRouteComponent,
})

function EnvironmentRouteComponent() {
  return (
    <PageShell>
      <EnvironmentPage />
    </PageShell>
  )
}

export default function EnvironmentPage() {
  const navigate = useNavigate()
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [name, setName] = useState('')
  const [isMt5, setIsMt5] = useState(false)
  const [server, setServer] = useState('')
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [saveError, setSaveError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const queryClient = useQueryClient()
  const { data: environments = [], isLoading, isError, error } = useQuery({
    queryKey: ['environments'],
    queryFn: fetchEnvironments,
  })

  const isFormValid = name.trim() !== '' &&
                      (!isMt5 || (server.trim() !== '' && login.trim() !== '' && password.trim() !== ''));

  const handleCloseModal = () => {
    setIsModalOpen(false)
    setName('')
    setIsMt5(false)
    setServer('')
    setLogin('')
    setPassword('')
    setSaveError(null)
  }



  const handleIntegerChange = (val: string, setter: (v: string) => void) => {
    const cleaned = val.replace(/[^0-9]/g, '')
    setter(cleaned)
  }

  const handleCreateEnvironment = async () => {
    if (!name.trim() || isSaving) return
    setIsSaving(true)
    setSaveError(null)
    try {
      await createEnvironment({
        name: name.trim(),
        isMt5,
        server: server.trim(),
        login: login.trim(),
        password,
      })
      await queryClient.invalidateQueries({ queryKey: ['environments'] })
      handleCloseModal()
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Could not save environment.')
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <div className="flex-1 bg-[#121214] flex flex-col min-h-0 select-none">
      {isLoading ? (
        <div className="pt-3 pl-5 text-sm text-gray-500">Loading environments…</div>
      ) : environments.length === 0 ? (
        <div className="pt-3 pl-5 flex flex-col items-start justify-start gap-4">
          {/* Top left text */}
          <div className="text-left">
            <h1 className="font-medium text-gray-400 font-sans tracking-wide">
              {isError ? `Could not load environments: ${error instanceof Error ? error.message : 'unknown error'}` : "There isn't any environment."}
            </h1>
          </div>

          <button
            type="button"
            onClick={() => setIsModalOpen(true)}
            className="liquid-glass-btn liquid-glass-btn-no-grow liquid-glass-btn-rounded-lg px-3 py-1.5 text-xs font-bold"
          >
            Create one
          </button>
        </div>
      ) : (
        <>
          <div className="flex-1 pt-5 pr-5 pb-5 pl-[50px] overflow-y-auto">
            <div className="mb-5 flex items-center gap-5 select-none">
              <h1 className="font-semibold text-gray-200 text-sm tracking-wide">
                Environments
              </h1>
              <button
                type="button"
                onClick={() => setIsModalOpen(true)}
                className="liquid-glass-btn liquid-glass-btn-no-grow liquid-glass-btn-rounded-lg px-2 py-1 text-xs font-bold"
              >
                Create
              </button>
            </div>

            {/* Styled Table matching Active Positions Table on March page */}
            <div className="border border-[#212124] bg-[#1A1A1E] overflow-hidden shadow-2xl shadow-black/40 w-full max-w-[350px] select-text">
              <table className="min-w-full table-fixed text-left border-collapse text-xs">
                <thead>
                  <tr className="bg-[#28282D] border-b border-[#212124] text-gray-400 font-medium tracking-wide text-[10px] uppercase select-none">
                    <th className="py-3 pl-6 w-[30%]">Name</th>
                    <th className="py-3 px-3 w-[30%]">MT5</th>
                    <th className="py-3 px-3 w-[30%]">Account</th>
                    <th className="py-3 px-3 w-[10%] text-right pr-6"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-900/40 bg-gray-950/10">
                  {environments.map((env) => (
                    <tr
                      key={env.id}
                      className="border-b border-[#212124] last:border-0 text-gray-300"
                    >
                      {/* Name */}
                      <td className="py-3.5 pl-6 font-semibold text-gray-200 truncate" title={env.name}>
                        {env.name}
                      </td>

                      {/* MT5 Account check */}
                      <td className="py-3.5 px-3">
                        {env.isMt5 ? (
                          <p className='font-semibold text-gray-300'>MT5</p>
                        ) : (
                          <p className='font-semibold text-gray-300'>No</p>
                        )}
                      </td>

                      {/* Account Details */}
                      <td className="py-3.5 px-3">
                        {env.isMt5 ? (
                          <div className="truncate">
                            <div className="font-semibold text-gray-300 truncate" title={env.server}>
                              {env.server}
                            </div>
                            <div className="text-[10px] text-gray-500 font-mono mt-0.5">
                              #{env.login}
                            </div>
                          </div>
                        ) : (
                          <span className="text-gray-600 italic">—</span>
                        )}
                      </td>

                      {/* Right Arrow Button */}
                      <td className="py-3.5 px-3 text-right pr-6">
                        <button
                          className="w-7 h-7 liquid-glass-btn"
                          title="View Details"
                          onClick={(e) => {
                            e.stopPropagation();
                            void navigate({
                              to: '/environment/$environmentId',
                              params: { environmentId: String(env.id) },
                            });
                          }}
                        >
                          <svg
                            width="12"
                            height="12"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2.5"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          >
                            <line x1="5" y1="12" x2="19" y2="12" />
                            <polyline points="12 5 19 12 12 19" />
                          </svg>
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {/* Modal Dialog */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center backdrop-blur-[6px] bg-black/50">
          <div className="relative z-10 w-[550px] h-[400px] bg-[#121214] rounded-lg shadow-2xl flex flex-col overflow-hidden">
            {/* Header / Title Section */}
            <div className="flex justify-between items-center px-4 py-3 border-b border-[#212124] select-none">
              <h2 className="text-white font-bold text-sm">Create Environment</h2>
              <div className="flex items-center gap-5">
                <button
                  onClick={handleCloseModal}
                  className="text-gray-400 hover:text-gray-200 font-bold text-xs select-none cursor-pointer transition-colors"
                >
                  Cancel
                </button> 
                <button
                  type="button"
                  onClick={handleCreateEnvironment}
                  disabled={!isFormValid || isSaving}
                  className="liquid-glass-btn liquid-glass-btn-no-grow liquid-glass-btn-rounded-lg disabled:cursor-not-allowed px-2 py-1 text-xs font-bold"
                >
                  {isSaving ? 'Saving…' : 'Create'}
                </button>
              </div>
            </div>

            {/* Modal Content Area */}
            <div className="flex-1 bg-[#121214] px-4 pt-3 pb-4 flex flex-col gap-3 overflow-y-auto select-none">
              <div className="flex flex-col gap-2">
                <label className="text-[10px] font-semibold tracking-widest uppercase text-gray-500">Name</label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="idk"
                  className="w-full bg-black/20 border border-[#212124] text-sm text-gray-200 px-3 py-2 outline-none transition-colors placeholder:text-gray-600 select-text"
                />
              </div>

              {saveError && (
                <p className="text-xs text-red-400" role="alert">{saveError}</p>
              )}



              {/* MT5 Account Checkbox */}
              <div onClick={() => setIsMt5(!isMt5)} className="flex items-center gap-3 cursor-pointer select-none">
                <span className="text-xs font-bold text-gray-400">Is this an MT5 Account?</span>
                                <div className={`w-[18px] h-[18px] rounded border border-[#212124] flex items-center justify-center transition-colors ${isMt5 ? 'bg-[#2563eb] border-[#1e3a8a]' : 'bg-black/20'}`}>
                  {isMt5 && (
                    <svg width="10" height="8" viewBox="0 0 9 7" fill="none">
                      <path d="M1 3.5L3 5.5L8 1" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  )}
                </div>
              </div>

              {/* Conditional MT5 Fields */}
              {isMt5 && (
                <>
                  <div className="flex flex-col gap-2">
                    <label className="text-[10px] font-semibold tracking-widest uppercase text-gray-500">Server</label>
                    <input
                      type="text"
                      value={server}
                      onChange={(e) => setServer(e.target.value)}
                      placeholder="idk"
                      className="w-full bg-black/20 border border-[#212124] text-sm text-gray-200 px-3 py-2 outline-none transition-colors placeholder:text-gray-600 select-text"
                    />
                  </div>

                  <div className="flex flex-col gap-2">
                    <label className="text-[10px] font-semibold tracking-widest uppercase text-gray-500">Login</label>
                    <input
                      type="text"
                      value={login}
                      onChange={(e) => handleIntegerChange(e.target.value, setLogin)}
                      placeholder="000"
                      className="w-full bg-black/20 border border-[#212124] text-sm text-gray-200 px-3 py-2 outline-none transition-colors placeholder:text-gray-600 select-text"
                    />
                  </div>

                  <div className="flex flex-col gap-2">
                    <label className="text-[10px] font-semibold tracking-widest uppercase text-gray-500">Password</label>
                    <input
                      type="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="••••••"
                      className="w-full bg-black/20 border border-[#212124] text-sm text-gray-200 px-3 py-2 outline-none transition-colors placeholder:text-gray-600 select-text"
                    />
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}

    </div>
  )
}
