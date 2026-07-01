import { resolve } from 'node:path'
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
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        'session-bridge': resolve(__dirname, 'session-bridge.html'),
        'session-keeper': resolve(__dirname, 'src/session-keeper-main.ts'),
      },
      output: {
        entryFileNames: (chunk) => {
          if (chunk.name === 'session-keeper') return 'session-keeper.js'
          return 'assets/[name]-[hash].js'
        },
      },
    },
  },
})
