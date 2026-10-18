import { adminClient } from 'better-auth/client/plugins'
import { createAuthClient } from 'better-auth/react'

export const authClient = createAuthClient({ plugins: [adminClient()] })
export const signIn = () => authClient.signIn.social({ provider: 'google', callbackURL: location.pathname })
