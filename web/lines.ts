// Rewrites a newline-delimited stream one line at a time, however the chunks happen to split lines (or characters).
export function mapLines(body: ReadableStream<Uint8Array>, fn: (line: string) => Promise<string>) {
  const dec = new TextDecoder(), enc = new TextEncoder()
  let buf = ''
  return body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
    async transform(chunk, out) {
      buf += dec.decode(chunk, { stream: true })
      for (let i; (i = buf.indexOf('\n')) >= 0;) {
        const line = buf.slice(0, i)
        buf = buf.slice(i + 1)
        out.enqueue(enc.encode(await fn(line) + '\n'))
      }
    },
    flush(out) { if (buf) out.enqueue(enc.encode(buf)) },
  }))
}
