interface Props {
  checked: boolean
}

export default function LiquidGlassSwitch({ checked }: Props) {
  return (
    <span
      aria-hidden="true"
      className={`liquid-glass-switch ${checked ? 'liquid-glass-switch-on' : ''}`}
    >
      <span className="liquid-glass-switch-thumb" />
    </span>
  )
}
