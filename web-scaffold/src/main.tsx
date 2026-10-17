import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import '@fontsource-variable/inter'
import '@fontsource-variable/jetbrains-mono'
import './index.css' // Tailwind; imported before tokens.css so tokens win
import './styles/tokens.css'
import App from './App'

// Registers the theme and the chart/component set. Imported for its side effect
// only, and from `./lib/echarts` rather than from `'echarts'`, so the whole
// library is never pulled in - see the note at the top of that file.
import './lib/echarts'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      // The API sends `Cache-Control: max-age=300`, and this matches it. Two
      // different freshness windows would mean the browser and the server
      // disagreeing about whether a response is current.
      staleTime: 5 * 60_000,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
)
