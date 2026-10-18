import { betterAuth } from 'better-auth'
import { admin } from 'better-auth/plugins'
import { Pool } from 'pg'

export const pool = new Pool({ connectionString: process.env.DATABASE_URL })

// Google has verified the address, so the domain proves the person is staff. STAFF_DOMAIN unset: nobody is.
export const isStaff = (u?: { email: string, emailVerified: boolean } | null) =>
  !!u?.emailVerified && !!process.env.STAFF_DOMAIN && u.email.toLowerCase().endsWith('@' + process.env.STAFF_DOMAIN.toLowerCase())

export const auth = betterAuth({
  database: pool,
  socialProviders: {
    google: { clientId: process.env.GOOGLE_CLIENT_ID!, clientSecret: process.env.GOOGLE_CLIENT_SECRET! },
  },
  plugins: [admin()],
  databaseHooks: {
    user: { create: { before: async user => ({ data: { ...user, role: isStaff(user) ? 'admin' : 'user' } }) } },
  },
})
