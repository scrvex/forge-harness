// simulate the workflow with fake agents: dependency order, slots, red-then-green acceptance
import { body } from "./swarm-check.mjs"
const calls = []; let running = 0, peak = 0, acceptN = 0
const agent = async (prompt, opts) => {
  running++; peak = Math.max(peak, running); calls.push(opts.label)
  await new Promise(r => setTimeout(r, 5)); running--
  if (opts.label.startsWith('swarm_accept')) { acceptN++; return acceptN === 1
    ? { green: false, failing: [{ path: 'b.md', problems: ['no: Patreon'] }], for_reviewer: [], report: 'r', command: 'c' }
    : { green: true, failing: [], for_reviewer: ['reads like a memo'], report: 'r', command: 'c' } }
  if (opts.label === 'verifier2') return { verdict: 'APPROVE', findings: [] }
  const path = opts.label.replace(/^redo \d+: /, '')
  return { status: 'done', path, summary: 's', question: '' }
}
const args = { order: 'N010', plugin: '/p', root: '/r', manifestPath: '/r/plans/N010.json', staging: '/r/st', slots: 2,
  manifest: { cycle: 'N010', files: [ { path: 'a.md', tier: 'LOCAL' }, { path: 'b.md', tier: 'HEAVY', depends_on: ['a.md'] },
    { path: 'c.md', tier: 'FAST' }, { path: 'd.md', tier: 'FAST' } ] } }
const out = await body(args, agent, () => {}, () => {})
console.log(JSON.stringify({ calls, peak, review: out.review.verdict, next: out.next.slice(0, 40) }))
if (calls.indexOf('b.md') < calls.indexOf('a.md')) throw new Error('dependency order broken')
if (peak > 2) throw new Error('slots exceeded: ' + peak)
