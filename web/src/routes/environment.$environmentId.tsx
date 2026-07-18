import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { createEnvironmentRule, fetchBacktests, fetchEnvironmentRules, fetchEnvironments, updateEnvironmentRule, deleteEnvironmentRule, deleteBacktest } from '../api'
import { type Environment } from '../types'
import BacktestResultSidebar from '../components/backtests/BacktestResultSidebar'
import PageShell from '../components/layout/PageShell'
import ModalShell from '../components/ui/ModalShell'

export const Route = createFileRoute('/environment/$environmentId')({
  component: EnvironmentDetailRouteComponent,
})

function EnvironmentDetailRouteComponent() {
  const { environmentId } = Route.useParams()
  const navigate = useNavigate()
  const { data: environments = [], isLoading, isError, error } = useQuery({
    queryKey: ['environments'],
    queryFn: fetchEnvironments,
  })
  const selectedEnv = environments.find((environment) => environment.id === Number(environmentId))

  if (isLoading) {
    return <div className="flex flex-1 pt-3 pl-5 text-sm text-gray-500">Loading environment…</div>
  }

  if (isError || !selectedEnv) {
    return (
      <div className="flex flex-1 flex-col items-start gap-3 pt-5 pl-5">
        <p className="text-sm text-red-400">
          {isError
            ? `Could not load environment: ${error instanceof Error ? error.message : 'unknown error'}`
            : 'Environment not found.'}
        </p>
        <button
          type="button"
          className="liquid-glass-btn liquid-glass-btn-no-grow liquid-glass-btn-rounded-lg px-3 py-1.5 text-xs font-bold"
          onClick={() => void navigate({ to: '/environment' })}
        >
          Back to environments
        </button>
      </div>
    )
  }

  return (
    <PageShell>
      <EnvironmentDetailPage
        selectedEnv={selectedEnv}
        onBack={() => void navigate({ to: '/environment' })}
      />
    </PageShell>
  )
}

interface EnvironmentDetailPageProps {
  selectedEnv: Environment
  onBack: () => void
}

type RuleType = 'spread' | 'slippage' | 'commission'

const ruleTypes: RuleType[] = ['spread', 'slippage', 'commission']
const ruleLabels: Record<RuleType, string> = {
  spread: 'Spread',
  slippage: 'Slippage',
  commission: 'Commission',
}

function rulePlaceholder(type: RuleType) {
  if (type === 'spread') return 'Full bid/ask spread in points'
  if (type === 'slippage') return 'Points per fill'
  return 'USD per lot/contract per side'
}

function numericWithDot(value: string) {
  const cleaned = value.replace(/[^0-9.]/g, '')
  const firstDot = cleaned.indexOf('.')
  return firstDot === -1
    ? cleaned
    : cleaned.slice(0, firstDot + 1) + cleaned.slice(firstDot + 1).replaceAll('.', '')
}

export default function EnvironmentDetailPage({ selectedEnv, onBack }: EnvironmentDetailPageProps) {
  const [isAddRulesModalOpen, setIsAddRulesModalOpen] = useState(false)
  const [ruleType, setRuleType] = useState<RuleType>('spread')
  const [value, setValue] = useState('')
  const [saveError, setSaveError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const backtestSidebarStorageKey = `environment_backtest_result_${selectedEnv.id}`
  const [selectedBacktestId, setSelectedBacktestId] = useState<number | null>(() => {
    const saved = sessionStorage.getItem(backtestSidebarStorageKey)
    return saved ? Number(saved) : null
  })
  const queryClient = useQueryClient()
  const { data: rules = [], isLoading } = useQuery({
    queryKey: ['environment-rules', selectedEnv.id],
    queryFn: () => fetchEnvironmentRules(selectedEnv.id),
  })
  const { data: backtests = [] } = useQuery({
    queryKey: ['backtests'],
    queryFn: fetchBacktests,
  })
  const environmentBacktests = backtests.filter((backtest) => backtest.environment_id === selectedEnv.id)
  const selectedBacktest = environmentBacktests.find((backtest) => backtest.id === selectedBacktestId) ?? null

  useEffect(() => {
    const saved = sessionStorage.getItem(`environment_backtest_result_${selectedEnv.id}`)
    setSelectedBacktestId(saved ? Number(saved) : null)
  }, [selectedEnv.id])

  const toggleBacktestSidebar = (backtestId: number) => {
    if (selectedBacktestId === backtestId) {
      sessionStorage.removeItem(backtestSidebarStorageKey)
      setSelectedBacktestId(null)
      return
    }
    sessionStorage.setItem(backtestSidebarStorageKey, String(backtestId))
    setSelectedBacktestId(backtestId)
  }

  // Action Menu state
  const [activeMenuRuleType, setActiveMenuRuleType] = useState<string | null>(null)

  // Edit states
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [editRuleType, setEditRuleType] = useState<RuleType>('spread')
  const [editValue, setEditValue] = useState('')
  const [editSaveError, setEditSaveError] = useState<string | null>(null)
  const [isEditing, setIsEditing] = useState(false)

  const isEditFormValid = editValue !== '' && editValue !== '.' && Number.isFinite(Number(editValue))

  const handleOpenEditModal = (rule: any) => {
    setEditRuleType(rule.type)
    setEditValue(String(rule.value))
    setEditSaveError(null)
    setIsEditModalOpen(true)
  }

  const closeEditModal = () => {
    setIsEditModalOpen(false)
    setEditValue('')
    setEditSaveError(null)
  }

  const updateRule = async () => {
    if (!isEditFormValid || isEditing) return
    setIsEditing(true)
    setEditSaveError(null)
    try {
      await updateEnvironmentRule(selectedEnv.id, { type: editRuleType, value: Number(editValue) })
      await queryClient.invalidateQueries({ queryKey: ['environment-rules', selectedEnv.id] })
      closeEditModal()
    } catch (error) {
      setEditSaveError(error instanceof Error ? error.message : 'Could not update rule.')
    } finally {
      setIsEditing(false)
    }
  }

  const handleDeleteRule = async (type: string) => {
    if (!window.confirm(`Are you sure you want to delete the ${type} rule?`)) return
    try {
      await deleteEnvironmentRule(selectedEnv.id, type)
      await queryClient.invalidateQueries({ queryKey: ['environment-rules', selectedEnv.id] })
    } catch (error) {
      alert(error instanceof Error ? error.message : 'Could not delete rule.')
    }
  }

  const handleDeleteBacktest = async (id: number) => {
    if (!window.confirm('Are you sure you want to delete this backtest?')) return
    try {
      await deleteBacktest(id)
      await queryClient.invalidateQueries({ queryKey: ['backtests'] })
      sessionStorage.removeItem(backtestSidebarStorageKey)
      setSelectedBacktestId(null)
    } catch (error) {
      alert(error instanceof Error ? error.message : 'Could not delete backtest.')
    }
  }

  const missingRuleTypes = ruleTypes.filter((type) => !rules.some((rule) => rule.type === type))
  const canAddRule = missingRuleTypes.length > 0
  const isFormValid = canAddRule && value !== '' && value !== '.' && Number.isFinite(Number(value))
  const openModal = () => {
    const firstAvailableType = missingRuleTypes[0]
    if (!firstAvailableType) return
    setRuleType(firstAvailableType)
    setValue('')
    setSaveError(null)
    setIsAddRulesModalOpen(true)
  }
  const closeModal = () => {
    setIsAddRulesModalOpen(false)
    setRuleType('spread')
    setValue('')
    setSaveError(null)
  }
  const createRule = async () => {
    if (!isFormValid || isSaving || !missingRuleTypes.includes(ruleType)) return
    setIsSaving(true)
    setSaveError(null)
    try {
      await createEnvironmentRule(selectedEnv.id, { type: ruleType, value: Number(value) })
      await queryClient.invalidateQueries({ queryKey: ['environment-rules', selectedEnv.id] })
      closeModal()
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : 'Could not save rule.')
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <>
      <div className="flex-1 overflow-y-auto pt-5 pl-[50px] flex flex-col items-start gap-8">
        <div className="-ml-10 flex items-center gap-3 select-none">
          <button onClick={onBack} className="w-7 h-7 liquid-glass-btn" title="Back to Environments">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="19" y1="12" x2="5" y2="12" />
              <polyline points="12 19 5 12 12 5" />
            </svg>
          </button>
          <h1 className="font-semibold text-gray-200 text-sm tracking-wide">{selectedEnv.name}</h1>
        </div>

        {isLoading ? (
          <p className="text-sm text-gray-500">Loading rules…</p>
        ) : rules.length === 0 ? (
          <div className="flex flex-col items-start gap-4">
            <h2 className="font-medium text-gray-400 font-sans tracking-wide">This env does not have any rules.</h2>
            <button
              type="button"
              onClick={openModal}
              className="liquid-glass-btn liquid-glass-btn-no-grow liquid-glass-btn-rounded-lg px-3 py-1.5 text-xs font-bold"
            >
              Create one
            </button>
          </div>
        ) : (
          <div className="">
            <div className="flex items-center gap-4 mb-4">
              <h2 className="font-semibold text-gray-200 text-sm tracking-wide">Rules</h2>
              <button
                type="button"
                onClick={openModal}
                disabled={!canAddRule}
                className="liquid-glass-btn liquid-glass-btn-no-grow liquid-glass-btn-rounded-lg disabled:cursor-not-allowed px-2 py-1 text-xs font-bold"
              >
                Add
              </button>
            </div>
            <div className="border border-[#212124] bg-[#1A1A1E] shadow-2xl shadow-black/40 w-full max-w-[250px] select-text">
              <table className="min-w-full table-fixed text-left border-collapse text-xs">
                <thead>
                  <tr className="bg-[#28282D] border-b border-[#212124] text-gray-400 font-medium tracking-wide text-[10px] uppercase select-none">
                    <th className="py-3 pl-6 w-[45%]">Rule</th>
                    <th className="py-3 px-3 w-[35%]">Value</th>
                    <th className="py-3 px-3 w-[20%] text-right pr-6"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-900/40 bg-gray-950/10">
                  {rules.map((rule) => (
                    <tr key={rule.id} className="border-b border-[#212124] last:border-0 text-gray-300">
                      <td className="py-3.5 pl-6 font-semibold text-gray-200">{ruleLabels[rule.type as RuleType]}</td>
                      <td className="py-3.5 px-3 font-mono">{rule.value}</td>
                      <td className="py-3.5 px-3 text-right pr-6">
                        <div className="relative inline-block">
                          <button
                            className="w-7 h-7 liquid-glass-btn"
                            title="Actions"
                            onClick={(e) => {
                              e.stopPropagation();
                              setActiveMenuRuleType(activeMenuRuleType === rule.type ? null : rule.type);
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
                              <circle cx="12" cy="12" r="1" />
                              <circle cx="19" cy="12" r="1" />
                              <circle cx="5" cy="12" r="1" />
                            </svg>
                          </button>

                          {/* Dropdown Popup Menu */}
                          {activeMenuRuleType === rule.type && (
                            <>
                              {/* Invisible overlay to close dropdown on clicking outside */}
                              <div
                                className="fixed inset-0 z-30"
                                onClick={() => setActiveMenuRuleType(null)}
                              />
                              <div className="absolute left-full top-1/2 -translate-y-[16px] ml-3 z-40 w-24 liquid-glass-dropdown rounded-lg py-1 text-left flex flex-col">
                                {/* Triangle pointing to the button, aligned with Edit item */}
                                <div className="absolute -left-[6px] top-[16px] -translate-y-1/2 w-2.5 h-2.5 rotate-45 liquid-glass-dropdown-arrow" />
                                
                                <button
                                  onClick={() => {
                                    setActiveMenuRuleType(null);
                                    handleOpenEditModal(rule);
                                  }}
                                  className="relative z-10 w-full px-3 py-1.5 text-xs text-gray-200 hover:bg-white/10 transition-colors cursor-pointer text-left font-sans rounded-t-lg"
                                >
                                  Edit
                                </button>
                                <button
                                  onClick={() => {
                                    setActiveMenuRuleType(null);
                                    handleDeleteRule(rule.type);
                                  }}
                                  className="relative z-10 w-full px-3 py-1.5 text-xs text-red-400 hover:bg-white/10 transition-colors cursor-pointer text-left font-semibold font-sans rounded-b-lg"
                                >
                                  Delete
                                </button>
                              </div>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <div>
          <h2 className="font-semibold text-gray-200 text-sm tracking-wide mb-4">Backtests</h2>
          {environmentBacktests.length === 0 ? (
            <p className="text-sm text-gray-500">There is no backtest.</p>
          ) : (
            <div className="border border-[#212124] bg-[#1A1A1E] shadow-2xl shadow-black/40 w-[300px] select-text">
              <table className="min-w-full table-fixed text-left border-collapse text-xs">
                <thead>
                  <tr className="bg-[#28282D] border-b border-[#212124] text-gray-400 font-medium tracking-wide text-[10px] uppercase select-none">
                    <th className="py-3 pl-6 w-[20%]">ID</th>
                    <th className="py-3 pl-6 w-[70%]">name</th>
                    <th className="py-3 px-3 w-[10%] text-right pr-6"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-900/40 bg-gray-950/10">
                  {environmentBacktests.map((backtest) => (
                    <tr key={backtest.id} className="border-b border-[#212124] last:border-0 text-gray-300">
                      <td className="py-3.5 pl-6 font-mono">{backtest.id}</td>
                      <td className="py-3.5 pl-6 font-semibold text-gray-200">{backtest.strategy.replaceAll('_', ' ')}</td>
                      <td className="py-3.5 px-3 text-right pr-6">
                        <button
                          className="w-7 h-7 liquid-glass-btn"
                          title={selectedBacktestId === backtest.id ? 'Close Backtest Details' : 'View Backtest Details'}
                          onClick={(e) => {
                            e.stopPropagation();
                            toggleBacktestSidebar(backtest.id)
                          }}
                        >
                          {selectedBacktestId === backtest.id ? (
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                              <line x1="18" y1="6" x2="6" y2="18" />
                              <line x1="6" y1="6" x2="18" y2="18" />
                            </svg>
                          ) : (
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
                          )}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {selectedBacktest && (
        <BacktestResultSidebar
          key={selectedBacktest.id}
          backtest={selectedBacktest}
          onDelete={() => handleDeleteBacktest(selectedBacktest.id)}
        />
      )}

      <ModalShell
        open={isAddRulesModalOpen}
        onClose={closeModal}
        title="Add Rule"
        closePosition="left"
        className="!bg-[#121214]"
        headerExtra={
          <div className="flex items-center ml-auto">
            <button
              type="button"
              onClick={createRule}
              disabled={!isFormValid || isSaving}
              className="liquid-glass-btn liquid-glass-btn-no-grow liquid-glass-btn-rounded-lg disabled:cursor-not-allowed px-2 py-1 text-xs font-bold mr-2"
            >
              {isSaving ? 'Saving…' : 'Create'}
            </button>
          </div>
        }
      >
        <div className="bg-[#121214] px-4 pt-3 pb-4 flex flex-col gap-3 select-none">
          <div className="flex flex-col gap-2">
            <label className="text-[10px] font-semibold tracking-widest uppercase text-gray-500">Rule</label>
            <select
              value={ruleType}
              onChange={(event) => setRuleType(event.target.value as RuleType)}
              style={{ colorScheme: 'dark' }}
              className="w-full bg-black/20 border border-[#212124] text-sm text-gray-200 px-3 py-2 outline-none transition-colors cursor-pointer select-text"
            >
              {missingRuleTypes.map((type) => (
                <option key={type} value={type}>{ruleLabels[type]}</option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-2">
            <label className="text-[10px] font-semibold tracking-widest uppercase text-gray-500">Value</label>
            <input type="text" inputMode="decimal" value={value} onChange={(event) => setValue(numericWithDot(event.target.value))} placeholder={rulePlaceholder(ruleType)} className="w-full bg-black/20 border border-[#212124] text-sm text-gray-200 px-3 py-2 outline-none transition-colors placeholder:text-gray-600 select-text" />
          </div>
          {saveError && <p className="text-xs text-red-400" role="alert">{saveError}</p>}
        </div>
      </ModalShell>

      {/* Edit Rule Modal Dialog */}
      <ModalShell
        open={isEditModalOpen}
        onClose={closeEditModal}
        title="Edit Rule"
        closePosition="left"
        className="!bg-[#121214]"
        headerExtra={
          <div className="flex items-center ml-auto">
            <button
              type="button"
              onClick={updateRule}
              disabled={!isEditFormValid || isEditing}
              className="liquid-glass-btn liquid-glass-btn-no-grow liquid-glass-btn-rounded-lg disabled:cursor-not-allowed px-2 py-1 text-xs font-bold mr-2"
            >
              {isEditing ? 'Saving…' : 'Save'}
            </button>
          </div>
        }
      >
        <div className="bg-[#121214] px-4 pt-4 pb-4 flex flex-col gap-3 select-none">
          <div className="flex flex-col gap-2">
            <label className="text-[10px] font-semibold tracking-widest uppercase text-gray-500">Rule</label>
            <input type="text" value={ruleLabels[editRuleType]} disabled className="w-full bg-[#1A1A1E] border border-[#212124] text-sm text-gray-400 px-3 py-2 outline-none select-none cursor-not-allowed font-semibold" />
          </div>
          <div className="flex flex-col gap-2">
            <label className="text-[10px] font-semibold tracking-widest uppercase text-gray-500">Value</label>
            <input type="text" inputMode="decimal" value={editValue} onChange={(event) => setEditValue(numericWithDot(event.target.value))} placeholder={rulePlaceholder(editRuleType)} className="w-full bg-black/20 border border-[#212124] text-sm text-gray-200 px-3 py-2 outline-none transition-colors placeholder:text-gray-600 select-text" />
          </div>
          {editSaveError && <p className="text-xs text-red-400" role="alert">{editSaveError}</p>}
        </div>
      </ModalShell>
    </>
  )
}
