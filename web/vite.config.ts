import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The Tailwind plugin is registered here rather than through a
// tailwind.config.js: Tailwind v4 reads its theme from CSS and its content
// sources from the module graph, so there is no config file to keep in step
// with the token file.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    /* An explicit budget rather than the default 500kB, with the reasoning kept
     * next to it so the number is a decision and not a silenced warning.
     *
     * The main chunk is dominated by ECharts, which is roughly 800kB minified
     * even when only the Home page's chart types are registered, plus React
     * (~140kB) and Framer Motion (~120kB). That is the price of the chart
     * library docs/UI-BLUEPRINT.md §2 chose, and it is close to the floor for
     * this stack.
     *
     * Two structural reductions are already in place: `lib/echarts.ts`
     * registers only the types the Home page draws (registering all of §8
     * measured 1.33MB against 1.18MB now), and every route except Home is
     * `React.lazy`, so React Flow, dnd-kit and cmdk are off the first load.
     *
     * The remaining lever is deferring ECharts registration until a chart
     * actually mounts, which is worth doing in U3 when there is a chart to
     * mount. Until then this figure is honest rather than inflated. */
    chunkSizeWarningLimit: 1300,
  },
  server: {
    // Cross-origin to :8000 is expected and allowed - `dwm/api/app.py` lists
    // both spellings of the dev origin, because browsers treat localhost and
    // 127.0.0.1 as different origins. A proxy is deliberately NOT used: it
    // would hide the CORS configuration that a real deployment also needs.
    port: 5173,
    strictPort: true,
  },
})
