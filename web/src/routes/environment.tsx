import { createFileRoute, Outlet } from '@tanstack/react-router'

export const Route = createFileRoute('/environment')({
  component: EnvironmentLayout,
})

function EnvironmentLayout() {
  return <Outlet />
}
