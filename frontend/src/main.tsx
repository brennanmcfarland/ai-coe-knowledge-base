import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router'
import './index.css'
import { Layout } from '@/components/Layout'
import { EditModeProvider } from '@/lib/editMode'
import { GraphPage } from '@/pages/GraphPage'
import { LoginPage } from '@/pages/LoginPage'
import { NodePage } from '@/pages/NodePage'
import { ProjectsPage } from '@/pages/ProjectsPage'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } },
})

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { index: true, element: <Navigate to="/projects" replace /> },
      { path: 'login', element: <LoginPage /> },
      { path: 'projects', element: <ProjectsPage /> },
      { path: 'projects/:pid', element: <GraphPage /> },
      { path: 'projects/:pid/nodes/:nid', element: <NodePage /> },
    ],
  },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <EditModeProvider>
        <RouterProvider router={router} />
      </EditModeProvider>
    </QueryClientProvider>
  </StrictMode>,
)
