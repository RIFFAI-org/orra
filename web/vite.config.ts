import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const server = 'http://localhost:3000'  // npm run server

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': server } },
})
