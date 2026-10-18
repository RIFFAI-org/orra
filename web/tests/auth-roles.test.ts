// Who becomes an admin at sign-up, and the STAFF_DOMAIN rule behind it (basic isStaff cases are in ../auth.test.ts).
// Local dev tests: from web/, run `node --test "tests/*.test.ts"`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { auth, isStaff } from '../auth.ts'

process.env.STAFF_DOMAIN = 'example.org'

type Created = { data: Record<string, unknown> }
const beforeCreate = auth.options.databaseHooks!.user!.create!.before as unknown as (user: Record<string, unknown>) => Promise<Created>
const signUp = async (email: string, emailVerified = true, extra: Record<string, unknown> = {}) =>
  (await beforeCreate({ name: 'N', email, emailVerified, ...extra })).data

test('a verified @example.org sign-up becomes an admin and keeps its other fields', async () => {
  assert.deepEqual(await signUp('dev@example.org', true, { image: 'pic.png' }),
    { name: 'N', email: 'dev@example.org', emailVerified: true, image: 'pic.png', role: 'admin' })
})

test('everyone else becomes a plain user', async () => {
  for (const [email, verified] of [['a@gmail.com', true], ['dev@example.org', false], ['a@example.org.evil.com', true], ['a@notexample.org', true],
                                   ['a@sub.example.org', true], ['example.org', true], ['', true]] as const) {
    assert.equal((await signUp(email, verified)).role, 'user', `${email} verified=${verified}`)
  }
})

test('a role sent along with the sign-up can neither grant nor withhold admin', async () => {
  assert.equal((await signUp('a@gmail.com', true, { role: 'admin' })).role, 'user')
  assert.equal((await signUp('dev@example.org', true, { role: 'user' })).role, 'admin')
})

test('the staff rule ignores case, needs the exact domain and tolerates no user', () => {
  assert.ok(isStaff({ email: 'Dev@Example.Org', emailVerified: true }))
  assert.ok(!isStaff({ email: 'dev@example.org ', emailVerified: true }))
  assert.ok(!isStaff({ email: 'dev@example.org', emailVerified: undefined as unknown as boolean }))
  assert.ok(!isStaff(undefined))
  assert.ok(!isStaff(null))
})
