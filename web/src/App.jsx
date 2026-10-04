import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Route, BrowserRouter as Router, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import Feed from './pages/Feed'
import HotspotDetail from './pages/HotspotDetail'
import Hotspots from './pages/Hotspots'
import Settings from './pages/Settings'
import Styles from './pages/Styles'
import Today from './pages/Today'
import { ToastProvider } from './toast'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 15000 },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <Router>
          <Layout>
            <Routes>
              <Route path="/" element={<Today />} />
              <Route path="/hotspots" element={<Hotspots />} />
              <Route path="/hotspots/:id" element={<HotspotDetail />} />
              <Route path="/feed" element={<Feed />} />
              <Route path="/styles" element={<Styles />} />
              <Route path="/settings" element={<Settings />} />
            </Routes>
          </Layout>
        </Router>
      </ToastProvider>
    </QueryClientProvider>
  )
}
