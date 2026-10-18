// Edge cases for mapLines (the basic split-across-chunks case is in ../lines.test.ts).
// Local dev tests: from web/, run `node --test "tests/*.test.ts"`.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mapLines } from '../lines.ts'

const enc = new TextEncoder()
const streamOf = (chunks: Uint8Array[]) => new ReadableStream<Uint8Array>({ start(c) { chunks.forEach(x => c.enqueue(x)); c.close() } })
const inPieces = (s: string, size: number) => {
  const bytes = enc.encode(s)
  return Array.from({ length: Math.ceil(bytes.length / size) }, (_, i) => bytes.slice(i * size, (i + 1) * size))
}
const run = (chunks: Uint8Array[], fn: (line: string) => Promise<string> = async l => l) => new Response(mapLines(streamOf(chunks), fn)).text()

test('an empty stream stays empty and the mapper is never called', async () => {
  let calls = 0
  assert.equal(await run([], async l => { calls++; return l }), '')
  assert.equal(calls, 0)
})

test('each line is mapped once without its newline, blank lines included', async () => {
  const seen: string[] = []
  const out = await run(inPieces('a\n\nb\n', 64), async l => { seen.push(l); return l.toUpperCase() })
  assert.deepEqual(seen, ['a', '', 'b'])
  assert.equal(out, 'A\n\nB\n')
})

test('an unterminated last line is passed through as it came, not mapped', async () => {
  assert.equal(await run(inPieces('one\ntwo', 64), async l => l.toUpperCase()), 'ONE\ntwo')
})

test('whatever the mapper returns gets exactly one newline back', async () => {
  assert.equal(await run(inPieces('a\nb\n', 64), async l => l === 'a' ? '' : l + '!'), '\nb!\n')
})

test('the result is the same however the bytes are cut, one byte at a time included', async () => {
  const payload = '{"a":"น้ำ"}\n😀 two\n\nlast\n'  // 3-byte Thai, a 4-byte emoji, a blank line
  for (let size = 1; size <= 14; size++) {
    assert.equal(await run(inPieces(payload, size), async l => `[${l}]`), '[{"a":"น้ำ"}]\n[😀 two]\n[]\n[last]\n', `chunks of ${size} bytes`)
  }
})

test('a long line spread over many chunks is still one line', async () => {
  const line = 'x'.repeat(64 * 1024)
  const seen: number[] = []
  const out = await run(inPieces(line + '\nnext\n', 1024), async l => { seen.push(l.length); return l })
  assert.deepEqual(seen, [64 * 1024, 4])
  assert.equal(out, line + '\nnext\n')
})

test('lines come out in order even when a later one is mapped faster', async () => {
  const delays: Record<string, number> = { slow: 30, fast: 0 }
  const out = await run(inPieces('slow\nfast\n', 64), async l => { await new Promise(ok => setTimeout(ok, delays[l])); return l })
  assert.equal(out, 'slow\nfast\n')
})

test('a mapper that throws fails the stream with its error', async () => {
  await assert.rejects(run(inPieces('a\nb\n', 64), async l => { if (l === 'b') throw new Error('save failed'); return l }), /save failed/)
})
