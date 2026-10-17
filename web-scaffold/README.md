# web-scaffold: how to start the front end

Run these in your project root (Node.js 18 or newer):

```
npm create vite@latest web -- --template react-ts
cd web
npm install
npm i react-router-dom @tanstack/react-query zustand framer-motion echarts echarts-for-react lucide-react cmdk @tanstack/react-table @xyflow/react @dnd-kit/core
npm i @fontsource-variable/inter @fontsource-variable/jetbrains-mono
```
Install Tailwind CSS using its current guide for Vite (tailwindcss.com, "Install Tailwind CSS with Vite"). Then copy this folder's `src/` files over `web/src/` (keep paths) and set `web/.env`:
```
VITE_API_URL=http://127.0.0.1:8000
```
Run: `npm run dev` (http://localhost:5173) and in another terminal `python -m dwm serve`.
Use `http://localhost:5173/?mock=1` to read dev-only mock files from `public/mock/ui/`.

`src/main.tsx` and `src/App.tsx` in this scaffold already wire the providers, router and shell. Pages other than Home are placeholders for OpenCode to build in phases U3 to U7.
