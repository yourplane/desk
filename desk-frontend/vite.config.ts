import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiPort = process.env.DESK_API_PORT ? Number(process.env.DESK_API_PORT) : 8000

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': `http://localhost:${apiPort}`,
    },
  },
})
