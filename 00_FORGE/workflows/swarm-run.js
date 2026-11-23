export const meta = {
  name: 'swarm-run',
  description: 'Forge swarm cycle (K26-K27): one worker per manifest file in dependency order, scripted acceptance, one reviewer pass',
  whenToUse: 'A Forge order whose K26 check said swarm and whose manifest the Lead has written; the Lead passes the parsed manifest in args and applies the result itself.',
  phases: [
    { title: 'Workers', detail: 'one Forge agent per file, staging only, dependencies first, at most args.slots at a time' },
    { title: 'Acceptance', detail: 'swarm_accept.py; a red file is redone once more with its problems (K27)' },
    { title: 'Review', detail: 'one verifier2 pass over the lines no script can check' },
  ],
}

// args (the Lead reads the manifest and passes it; a workflow has no file access):
// { order: "N010", plugin: "<plugin folder>", root: "<project root>", manifestPath: "<circle>/plans/N010.json",
//   staging: "<state>/staging/N010", slots: 2, manifest: { cycle, files: [{ path, tier, agent?, goal, tasks, context,
//   dod, depends_on }] } }
// Returns { workers, acceptance, review } - the Lead judges from it and runs `swarm_accept.py ... --apply` itself (K27).

const A = args || {}
const need = ['order', 'plugin', 'root', 'manifestPath', 'staging', 'manifest']
const missing = need.filter(k => !A[k])
if (missing.length) throw new Error(`swarm-run: args missing ${missing.join(', ')} - see the comment at the top of the script`)
const files = (A.manifest.files || []).filter(f => f && f.path)
if (!files.length) throw new Error('swarm-run: the manifest has no files')
const SLOTS = Math.max(1, Math.min(Number(A.slots) || 2, 4)) // K28: Claude sub-agents <= the machine's slots (2 by default)
const byPath = Object.fromEntries(files.map(f => [f.path, f]))
for (const f of files) for (const d of f.depends_on || []) {
  if (!byPath[d]) throw new Error(`swarm-run: ${f.path} depends on ${d}, which is not in the manifest`)
}

const AGENT_BY_TIER = { HEAVY: 'forge:executor', FAST: 'forge:executor-fast', LOCAL: 'forge:executor-fast', LEAD: 'forge:executor' }
const WORKER = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['done', 'blocked', 'failed'] },
    path: { type: 'string' },
    summary: { type: 'string' },
    question: { type: 'string' },
  },
  required: ['status', 'path', 'summary', 'question'],
}
const ACCEPT = {
  type: 'object',
  properties: {
    green: { type: 'boolean' },
    failing: { type: 'array', items: { type: 'object', properties: { path: { type: 'string' }, problems: { type: 'array', items: { type: 'string' } } }, required: ['path', 'problems'] } },
    for_reviewer: { type: 'array', items: { type: 'string' } },
    report: { type: 'string' },
    command: { type: 'string' },
  },
  required: ['green', 'failing', 'for_reviewer', 'report', 'command'],
}
const REVIEW = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['APPROVE', 'CHANGES', 'REJECT'] },
    findings: { type: 'array', items: { type: 'string' } },
  },
  required: ['verdict', 'findings'],
}

// a small semaphore: the workflow's own cap is higher than this PC's slots (K28)
let free = SLOTS
const waiting = []
async function slot(fn) {
  if (free > 0) free -= 1
  else await new Promise(resolve => waiting.push(resolve))
  try {
    return await fn()
  } finally {
    const next = waiting.shift()
    if (next) next()
    else free += 1
  }
}

function brief(f, problems) {
  const lines = [
    `Forge order ${A.order}, swarm cycle ${A.manifest.cycle || A.order}: you produce ONE file of the manifest ${A.manifestPath}.`,
    `Write it ONLY to ${A.staging}/${f.path} (create folders as needed). Never touch the project tree, STATE.md, the index or the plugin.`,
    `Goal: ${f.goal || '(see tasks)'}`,
    `Tasks:\n${(f.tasks || []).map(t => `- ${t}`).join('\n') || '- (none beyond the goal)'}`,
    `Read-only context (paths under ${A.root}; files of earlier stages are in ${A.staging}/ under the same path):\n${(f.context || []).map(c => `- ${c}`).join('\n') || '- (none)'}`,
    `DoD - the acceptance script checks the scriptable lines, one reviewer the rest:\n${(f.dod || []).map(d => `- ${d}`).join('\n') || '- (none)'}`,
    'UTF-8 without BOM, LF line endings; the user\'s language for user-facing text; every fact with its source; anything not from the context marked "(assumption)"; never invent numbers.',
    'If the brief can be read two ways, do not guess inside the file: status blocked, the question and your best guess in `question`.',
  ]
  if (f.tier === 'LOCAL') {
    lines.push(`This stage is LOCAL tier: produce it with the local model (python ${A.plugin}/tools/local_jobs.py --help, or local_exec.py), not by writing it yourself; if Ollama is unavailable, status blocked.`)
  }
  if (problems && problems.length) {
    lines.push(`The acceptance script refused the previous version. Fix exactly these problems:\n${problems.map(p => `- ${p}`).join('\n')}`)
  }
  lines.push('Reply only through the schema: status, the path you wrote, a short summary, the question (empty unless blocked).')
  return lines.join('\n\n')
}

function work(f, problems, round) {
  return slot(() => agent(brief(f, problems), {
    label: `${round > 1 ? `redo ${round}: ` : ''}${f.path}`,
    phase: 'Workers',
    agentType: f.agent ? `forge:${f.agent}` : (AGENT_BY_TIER[f.tier] || 'forge:executor'),
    schema: WORKER,
  }))
}

phase('Workers')
log(`${files.length} file(s), at most ${SLOTS} worker(s) at a time`)
const started = {}
function run(path) {
  if (!started[path]) {
    const f = byPath[path]
    started[path] = Promise.all((f.depends_on || []).map(run)).then(async deps => {
      const blocked = deps.filter(d => !d || d.status !== 'done')
      if (blocked.length) return { status: 'blocked', path, summary: '', question: `an earlier stage did not finish: ${blocked.map(d => (d && d.path) || '?').join(', ')}` }
      return (await work(f, null, 1)) || { status: 'failed', path, summary: '', question: 'the worker stopped' }
    })
  }
  return started[path]
}
const workers = await Promise.all(files.map(f => run(f.path)))
const notDone = workers.filter(w => w.status !== 'done')
if (notDone.length) {
  log(`not done: ${notDone.map(w => `${w.path} (${w.status})`).join(', ')} - acceptance still runs and will name them`)
}

phase('Acceptance')
const acceptPrompt = extra => [
  `Run in ${A.root}: python ${A.plugin}/tools/swarm_accept.py ${A.manifestPath} --staging ${A.staging}`,
  'Do not add --apply and do not edit any file: you only run the check and report it.',
  `Report through the schema: green (the script's verdict), failing (each red file with its problems, as printed), for_reviewer (the human DoD lines the script routed to the reviewer, as printed), report (the path of the JSON report it wrote), command (the exact command you ran).${extra || ''}`,
].join('\n')
let acceptance = await agent(acceptPrompt(), { label: 'swarm_accept', phase: 'Acceptance', agentType: 'forge:executor-fast', effort: 'low', schema: ACCEPT })
let rounds = 1
while (acceptance && !acceptance.green && rounds < 2) { // K27: a red file is redone at most twice in all, then HOLD + lesson
  rounds += 1
  const redo = acceptance.failing.filter(x => byPath[x.path])
  log(`round ${rounds}: redoing ${redo.map(x => x.path).join(', ') || 'nothing that is in the manifest'}`)
  await Promise.all(redo.map(x => work(byPath[x.path], x.problems, rounds)))
  acceptance = await agent(acceptPrompt(` This is acceptance round ${rounds}.`), { label: `swarm_accept ${rounds}`, phase: 'Acceptance', agentType: 'forge:executor-fast', effort: 'low', schema: ACCEPT })
}
if (!acceptance) return { workers, acceptance: null, review: null, next: 'the acceptance run failed - run swarm_accept.py yourself' }
if (!acceptance.green) {
  return { workers, acceptance, review: null, next: `still red after ${rounds} rounds: HOLD the order with a lesson (K21, K27)` }
}

phase('Review')
let review = null
if (acceptance.for_reviewer.length) {
  review = await agent([
    `Review swarm cycle ${A.manifest.cycle || A.order} of Forge order ${A.order}. The files are in ${A.staging}/ (paths as in ${A.manifestPath}).`,
    `Judge ONLY these lines, which no script can check:\n${acceptance.for_reviewer.map(l => `- ${l}`).join('\n')}`,
    'Cite file:line for every finding; "nothing found" is a valid verdict. You are read-only.',
  ].join('\n\n'), { label: 'verifier2', phase: 'Review', agentType: 'forge:verifier2', schema: REVIEW })
} else {
  log('no human DoD lines: the scripted acceptance is the whole check')
}
return {
  workers,
  acceptance,
  review,
  next: review && review.verdict !== 'APPROVE'
    ? 'reviewer asked for changes: fix the named files in staging (or redo their stage), then run this workflow again or swarm_accept.py'
    : `the Lead applies: python ${A.plugin}/tools/swarm_accept.py ${A.manifestPath} --staging ${A.staging} --apply, then order.py done ${A.order} --commit`,
}
