import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/')({
  component: HomePage,
})

export default function HomePage() {
  return (
    <div className="flex-1 bg-[#121214]"></div>
  )
}
