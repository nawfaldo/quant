import { createFileRoute } from '@tanstack/react-router'
import PageShell from '../components/layout/PageShell'

export const Route = createFileRoute('/code')({
  component: CodeRouteComponent,
})

function CodeRouteComponent() {
  return (
    <PageShell>
      <CodePage />
    </PageShell>
  )
}

export default function CodePage() {
  return (
    <div className="flex-1 bg-[#121214]"></div>
  )
}
