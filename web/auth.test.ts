import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isStaff } from './auth.ts'

test('only verified accounts on STAFF_DOMAIN are staff', () => {
  process.env.STAFF_DOMAIN = 'example.org'
  assert.ok(isStaff({ email: 'a@example.org', emailVerified: true }))
  assert.ok(isStaff({ email: 'A@EXAMPLE.ORG', emailVerified: true }))
  assert.ok(!isStaff({ email: 'a@example.org', emailVerified: false }))
  assert.ok(!isStaff({ email: 'a@example.org.evil.com', emailVerified: true }))
  assert.ok(!isStaff({ email: 'a@notexample.org', emailVerified: true }))
  assert.ok(!isStaff({ email: 'a@sub.example.org', emailVerified: true }))
  assert.ok(!isStaff(null))
})

test('with STAFF_DOMAIN unset or empty, nobody is staff', () => {
  for (const d of [undefined, '']) {
    if (d === undefined) delete process.env.STAFF_DOMAIN; else process.env.STAFF_DOMAIN = d
    assert.ok(!isStaff({ email: 'a@example.org', emailVerified: true }))
    assert.ok(!isStaff({ email: 'a@', emailVerified: true }))
  }
})
