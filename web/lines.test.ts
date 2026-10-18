import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mapLines } from './lines.ts'

test('lines are rewritten whole, even when chunks split a line or a Thai character', async () => {
  const bytes = new TextEncoder().encode('{"a":1}\n{"b":"น้ำ"}\ntail')
  const cuts = [3, 9, 13, 14, 20]  // 13/14 fall inside the 3-byte Thai characters
  const body = new ReadableStream<Uint8Array>({
    start(c) { [0, ...cuts].forEach((s, i) => c.enqueue(bytes.slice(s, cuts[i] ?? bytes.length))); c.close() },
  })
  const out = await new Response(mapLines(body, async l => l.includes('b') ? l.toUpperCase() : l)).text()
  assert.equal(out, '{"a":1}\n{"B":"น้ำ"}\ntail')
})
