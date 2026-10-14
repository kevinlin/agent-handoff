import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const html = readFileSync(join(root, 'assets', 'transcript-viewer.html'), 'utf8');
const fixture = (name) =>
  readFileSync(join(here, 'fixtures', 'transcript', name), 'utf8');

// Pull the page's own script blocks and run them, so the tests exercise the
// shipped code rather than a second copy that can drift.
function blockOf(id) {
  const m = html.match(new RegExp(`<script id="${id}"[^>]*>([\\s\\S]*?)</script>`));
  assert.ok(m, `missing script block ${id}`);
  return m[1];
}
// runInThisContext, not createContext: a separate VM realm gives arrays a
// different Array.prototype, and deepStrictEqual then rejects structurally
// equal values as "not reference-equal".
globalThis.window = globalThis.window || {};
vm.runInThisContext(blockOf('viewer-normalize'));
const { normalize } = globalThis.window.HandoffViewer;

test('folds started/completed pairs into one row at the first position', () => {
  const { rows } = normalize(fixture('codex-basic.jsonl'));
  const command = rows.filter(r => r.kind === 'command');
  assert.equal(command.length, 1);
  assert.equal(command[0].exit_code, 0);
  assert.equal(command[0].output, 'a\nb');
  assert.equal(command[0].pos, 2, 'keeps the item.started line position');
});

test('events with no item id each keep their own row', () => {
  const { rows } = normalize(fixture('codex-basic.jsonl'));
  assert.equal(rows.filter(r => r.kind === 'lifecycle').length, 3);
});

test('an interior malformed line is visible, not dropped', () => {
  const { rows } = normalize(fixture('codex-basic.jsonl'));
  const bad = rows.filter(r => r.kind === 'unparsed');
  assert.equal(bad.length, 1);
  assert.equal(bad[0].line, 4);
  assert.match(bad[0].text, /NOT JSON AT ALL/);
});

test('a truncated trailing line is reported as partial, not as a row', () => {
  const { rows, partial } = normalize(fixture('codex-basic.jsonl'));
  assert.equal(partial, true);
  assert.equal(rows.filter(r => r.kind === 'unparsed').length, 1);
});

test('web_search keeps the full query list, not the truncated one', () => {
  const { rows } = normalize(fixture('codex-basic.jsonl'));
  const search = rows.find(r => r.kind === 'web_search');
  assert.deepEqual(search.queries, ['full one', 'full two']);
});

test('claude tool_use joins its tool_result and keeps both halves', () => {
  const { rows } = normalize(fixture('claude-tools.jsonl'));
  const bash = rows.find(r => r.tool_use_id === 'tu1');
  assert.equal(bash.kind, 'command');
  assert.equal(bash.command, 'pytest -q');
  assert.equal(bash.output, '2 passed');
});

test('a failed Edit is an error row, never a file change', () => {
  const { rows } = normalize(fixture('claude-tools.jsonl'));
  const edit = rows.find(r => r.tool_use_id === 'tu2');
  assert.equal(edit.is_error, true);
  assert.notEqual(edit.kind, 'file_change');
});

test('an unrecognized block inside a known assistant event survives', () => {
  const { rows } = normalize(fixture('claude-tools.jsonl'));
  assert.ok(rows.some(r => r.kind === 'unknown' && r.type === 'weird_block'));
});

test('the final answer appears once, not twice', () => {
  const { rows } = normalize(fixture('claude-tools.jsonl'));
  const finals = rows.filter(r => r.kind === 'agent' && r.text === 'All set.');
  assert.equal(finals.length, 1);
  assert.equal(finals[0].final, true);
});

test('rows come back in source order', () => {
  const { rows } = normalize(fixture('codex-basic.jsonl'));
  const positions = rows.map(r => r.pos);
  assert.deepEqual(positions, [...positions].sort((a, b) => a - b));
});

// The render block needs marked, so build a context with both.
let cachedViewer = null;
function renderContext() {
  if (cachedViewer) return cachedViewer;
  globalThis.window = globalThis.window || {};
  vm.runInThisContext(blockOf('vendor-marked'));   // defines `marked`
  vm.runInThisContext(blockOf('viewer-normalize'));
  vm.runInThisContext(blockOf('viewer-render'));
  cachedViewer = globalThis.window.HandoffViewer;
  return cachedViewer;
}

// Minimal DOM for the real mount/renderRow path, including prompt labels and
// markdown bodies. No browser or duplicate stream-assembly implementation.
function mountedStream(payload) {
  class Element {
    children = [];
    className = '';
    classList = { add: name => { this.className += ` ${name}`; } };
    set textContent(value) { this.text = value; this.children = []; }
    get textContent() { return this.text; }
    append(...nodes) { this.children.push(...nodes); }
    prepend(...nodes) { this.children.unshift(...nodes); }
    after() {} // Timestamp placement does not affect prompt counting.
  }
  const elements = new Map();
  const previousDocument = globalThis.document;
  const previousNode = globalThis.Node;
  globalThis.Node = Element;
  globalThis.document = {
    createElement: () => new Element(),
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new Element());
      return elements.get(id);
    },
  };
  try {
    renderContext().mount(payload);
    return elements.get('stream').children;
  } finally {
    globalThis.document = previousDocument;
    globalThis.Node = previousNode;
  }
}

function assertOnePrompt(payload, text) {
  const prompts = mountedStream(payload).filter(row => row.className.split(' ').includes('user'));
  assert.equal(prompts.length, 1);
  assert.equal(prompts[0].children[0].textContent, 'prompt');
  assert.equal(prompts[0].innerHTML, renderContext().renderMarkdown(text));
}

// The renderer may only emit markup on these allowlists. Asserting the
// structure beats scanning for "onerror": an escaped &lt;img onerror=…&gt;
// is inert text, and a substring scan flags it as a false positive.
const ALLOWED_TAGS = new Set(['p','br','hr','a','img','em','strong','del','code',
  'pre','blockquote','ul','ol','li','h1','h2','h3','h4','h5','h6',
  'table','thead','tbody','tr','th','td']);
const ALLOWED_ATTRS = new Set(['href','src','alt','title','class','rel','align']);
const SAFE_SCHEME = /^(https?:|mailto:|#)/i;

function markupViolations(rendered) {
  const found = [];
  for (const m of rendered.matchAll(/<\/?([a-zA-Z][\w-]*)((?:\s+[^<>]*)?)\/?>/g)) {
    const tag = m[1].toLowerCase();
    if (!ALLOWED_TAGS.has(tag)) { found.push(`tag <${tag}>`); continue; }
    for (const a of (m[2] || '').matchAll(
        /([a-zA-Z-]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/g)) {
      const name = a[1].toLowerCase();
      const value = a[2].replace(/^["']|["']$/g, '');
      if (!ALLOWED_ATTRS.has(name)) found.push(`attr ${name}`);
      if ((name === 'href' || name === 'src') && !SAFE_SCHEME.test(value.trim()))
        found.push(`scheme ${value}`);
    }
  }
  return found;
}

test('rendered agent markdown emits only allowlisted markup', () => {
  const HV = renderContext();
  const { rows } = HV.normalize(fixture('xss.jsonl'));
  const agent = rows.find(r => r.kind === 'agent');
  const rendered = HV.renderMarkdown(agent.text);
  assert.deepEqual(markupViolations(rendered), []);
});

test('hostile markup survives as visible text rather than vanishing', () => {
  const HV = renderContext();
  const { rows } = HV.normalize(fixture('xss.jsonl'));
  const rendered = HV.renderMarkdown(rows.find(r => r.kind === 'agent').text);
  assert.match(rendered, /&lt;img src=x onerror=alert\(1\)&gt;/);
});

test('javascript URLs never become links', () => {
  const HV = renderContext();
  const rendered = HV.renderMarkdown('[click](javascript:alert(1))');
  assert.doesNotMatch(rendered, /<a[^>]+href="javascript:/i);
  assert.match(rendered, /javascript:alert\(1\)/);  // shown as text
});

test('link text still renders through parseInline', () => {
  const HV = renderContext();
  const rendered = HV.renderMarkdown('[**bold** link](https://example.com)');
  assert.match(rendered, /<a href="https:\/\/example\.com"[^>]*><strong>bold<\/strong> link<\/a>/);
});

test('data URI images are rejected', () => {
  const HV = renderContext();
  const rendered = HV.renderMarkdown('![a](data:text/html;base64,PHN2Zz4=)');
  assert.deepEqual(markupViolations(rendered), []);
});

test('ordinary markdown still works', () => {
  const HV = renderContext();
  const rendered = HV.renderMarkdown('# H\n\n- a\n- b\n\n```js\nlet x=1;\n```');
  assert.match(rendered, /<h1>H<\/h1>/);
  assert.match(rendered, /<li>a<\/li>/);
  assert.match(rendered, /<code class="language-js">/);
});

test('safeHref accepts the four schemes and rejects the rest', () => {
  const HV = renderContext();
  for (const ok of ['https://a.b', 'http://a.b', 'mailto:a@b.c', '#frag'])
    assert.ok(HV.safeHref(ok), ok);
  for (const bad of ['javascript:alert(1)', 'data:text/html,x', 'vbscript:x',
                     ' javascript:alert(1)', 'JaVaScRiPt:alert(1)'])
    assert.equal(HV.safeHref(bad), null, bad);
});

test('per-event timestamps are captured when the format supplies one', () => {
  // Claude stream-json carries `timestamp`; Codex carries none.
  const claude = [
    '{"type":"assistant","timestamp":"2026-09-08T04:10:00.000Z","message":{"content":[{"type":"text","text":"hi"}]}}',
    '{"type":"assistant","message":{"content":[{"type":"text","text":"no clock"}]}}'
  ].join('\n');
  const { rows } = normalize(claude);
  assert.equal(rows[0].time, '2026-09-08T04:10:00.000Z');
  assert.equal(rows[1].time, undefined, 'an event with no timestamp stays timeless');
});

test('codex logs stay timeless rather than inheriting a neighbour clock', () => {
  const { rows } = normalize(fixture('codex-basic.jsonl'));
  assert.ok(rows.every(r => r.time == null));
});

// --- Copilot ---------------------------------------------------------------
// The branch is chosen by the declared backend, because copilot's terminal
// event shares the `result` type name with claude stream-json.

test('a dropped copilot log renders one prompt and no unknown user.message', () => {
  const payload = { meta: {}, log_text: fixture('copilot-basic.jsonl') };
  const { rows } = normalize(payload.log_text);
  const users = rows.filter(row => row.kind === 'user');
  assert.equal(users.length, 1);
  assert.equal(users[0].text, 'Run the checks and write a note.');
  assert.equal(rows.filter(row => row.kind === 'unknown' && row.type === 'user.message').length, 0);
  assertOnePrompt(payload, users[0].text);
});

test('a generated copilot payload renders exactly one prompt row', () => {
  assertOnePrompt({
    meta: { backend: 'copilot' }, log_text: fixture('copilot-basic.jsonl'),
    prompt_text: 'Run the checks and write a note.\n',
  }, 'Run the checks and write a note.');
});

test('codex and claude still render one prompt row from prompt_text', () => {
  for (const [backend, name] of [['codex', 'codex-basic.jsonl'], ['claude', 'claude-tools.jsonl']]) {
    assertOnePrompt({ meta: { backend }, log_text: fixture(name), prompt_text: 'Run the checks.' },
      'Run the checks.');
  }
});

test('copilot folds execution_start/complete pairs by toolCallId', () => {
  const { rows } = normalize(fixture('copilot-basic.jsonl'), 'copilot');
  const bash = rows.filter(r => r.tool_call_id === 'call_A');
  assert.equal(bash.length, 1);
  assert.equal(bash[0].kind, 'command');
  assert.equal(bash[0].command, 'pytest -q');
  assert.equal(bash[0].output, '2 passed');
  assert.equal(bash[0].is_error, false);
  assert.equal(bash[0].pos, 8, 'keeps the execution_start line position');
});

test('copilot ephemeral events are dropped', () => {
  const { rows } = normalize(fixture('copilot-basic.jsonl'), 'copilot');
  const raws = rows.map(r => JSON.stringify(r.raw));
  assert.ok(raws.every(r => !r.includes('message_delta')));
  assert.ok(raws.every(r => !r.includes('tool_call_delta')));
  assert.ok(raws.every(r => !r.includes('"ephemeral"')));
});

test('copilot final_answer is the one agent row', () => {
  const { rows } = normalize(fixture('copilot-basic.jsonl'), 'copilot');
  const agents = rows.filter(r => r.kind === 'agent');
  assert.equal(agents.length, 1);
  assert.equal(agents[0].final, true);
  assert.match(agents[0].text, /^Suite passed/);
});

test('a failed copilot write is an error row, never a file change', () => {
  const { rows } = normalize(fixture('copilot-basic.jsonl'), 'copilot');
  const create = rows.find(r => r.tool_call_id === 'call_B');
  assert.equal(create.is_error, true);
  assert.equal(create.kind, 'error');
  assert.equal(create.denied, undefined);
  assert.match(create.text, /EACCES/);
});

test('a denied copilot tool call is typed as denied, not just failed', () => {
  const { rows } = normalize(fixture('copilot-basic.jsonl'), 'copilot');
  const denied = rows.find(r => r.tool_call_id === 'call_C');
  assert.equal(denied.denied, true);
  assert.equal(denied.is_error, true);
  assert.match(denied.output, /shell\(curl\)/);
});

test('copilot turn events and result become lifecycle rows', () => {
  const { rows } = normalize(fixture('copilot-basic.jsonl'), 'copilot');
  const labels = rows.filter(r => r.kind === 'lifecycle').map(r => r.label);
  assert.deepEqual(labels, ['turn started', 'turn completed', 'result']);
  const result = rows.find(r => r.label === 'result');
  assert.equal(result.detail, 'exit 0');
  assert.equal(result.usage.premiumRequests, 1);
});

test('copilot per-event timestamps are captured', () => {
  const { rows } = normalize(fixture('copilot-basic.jsonl'), 'copilot');
  assert.equal(rows[0].time, '2026-09-09T05:31:17.900Z');
});

test('a truncated copilot log reports partial and keeps the session error', () => {
  const { rows, partial } = normalize(fixture('copilot-truncated.jsonl'), 'copilot');
  assert.equal(partial, true);
  assert.equal(rows.filter(r => r.kind === 'unparsed').length, 0);
  const error = rows.find(r => r.kind === 'error');
  assert.match(error.text, /400 Unsupported value/);
  const open = rows.find(r => r.tool_call_id === 'call_Z');
  assert.equal(open.is_error, undefined, 'an unfinished tool call has no outcome');
});

test('a dropped copilot log with no meta.backend still parses as copilot', () => {
  // The drop path mounts with meta: {}, so the backend has to be inferred.
  const text = fixture('copilot-basic.jsonl');
  assert.equal(globalThis.window.HandoffViewer.inferBackend(text), 'copilot');
  assert.deepEqual(normalize(text), normalize(text, 'copilot'));
});

test('the backend is inferred from a unique type, never from result', () => {
  const HVn = globalThis.window.HandoffViewer;
  assert.equal(HVn.inferBackend(fixture('codex-basic.jsonl')), 'codex');
  assert.equal(HVn.inferBackend('{"type":"result","exitCode":0}'), null);
  assert.equal(HVn.inferBackend('{"type":"result","result":"done"}'), null);
});

test('one result line parses differently under each declared backend', () => {
  const line = '{"type":"result","result":"All done.","exitCode":0,"usage":{"premiumRequests":1}}';
  const copilot = normalize(line, 'copilot').rows;
  const claude = normalize(line, 'claude').rows;
  assert.deepEqual(copilot.map(r => r.kind), ['lifecycle']);
  assert.equal(copilot[0].detail, 'exit 0');
  assert.deepEqual(claude.map(r => r.kind), ['agent', 'lifecycle']);
  assert.equal(claude[0].text, 'All done.');
  assert.equal(claude[1].detail, undefined);
});

test('codex and claude fixtures parse exactly as they did before the copilot branch', () => {
  // Snapshot taken from the parser as it stood before this branch existed.
  // Compared through JSON, so an explicitly-undefined key reads as absent.
  const baseline = JSON.parse(fixture('baseline-rows.json'));
  const plain = (value) => JSON.parse(JSON.stringify(value));
  for (const [name, expected] of Object.entries(baseline)) {
    const text = fixture(name);
    assert.deepEqual(plain(normalize(text)), expected, `${name} with no declared backend`);
    assert.deepEqual(plain(normalize(text, 'codex')), expected, `${name} declared codex`);
    assert.deepEqual(plain(normalize(text, 'claude')), expected, `${name} declared claude`);
  }
});

test('cursor drops thinking and folds each tool call into one row', () => {
  const { rows } = normalize(fixture('cursor-basic.jsonl'), 'cursor');
  assert.ok(rows.every(r => (r.raw || {}).type !== 'thinking'));
  const shells = rows.filter(r => r.kind === 'command');
  assert.equal(shells.length, 3);
  const check = shells.find(r => r.command === 'bash check.sh');
  assert.equal(check.outcome, 'success');
  assert.equal(check.exit_code, 0);
  assert.equal(check.output, 'checks ok');
});

test('a cursor call id with an embedded newline still pairs its halves', () => {
  const { rows } = normalize(fixture('cursor-basic.jsonl'), 'cursor');
  assert.equal(rows.filter(r => r.kind === 'error').length, 0);
  const edit = rows.find(r => r.tool === 'editToolCall');
  assert.equal(edit.kind, 'file_change');
  assert.deepEqual(edit.changes, [{ path: 'tracked.txt', kind: 'update' }]);
  // The row keeps both halves, so the page's body shows the completion too.
  assert.equal(edit.raw.started.call_id, 'call_E\nfc_1');
  assert.equal(edit.raw.completed.subtype, 'completed');
  assert.deepEqual(edit.raw.completed.tool_call.editToolCall.result, { success: {} });
});

test('a rejected cursor call is a denial and a spawnError is a failure', () => {
  const { rows } = normalize(fixture('cursor-basic.jsonl'), 'cursor');
  const curl = rows.find(r => r.command === 'curl https://example.com');
  assert.equal(curl.outcome, 'rejected');
  assert.equal(curl.denied, true);
  assert.equal(curl.is_error, true);
  const spawn = rows.find(r => r.command === 'cat ../outside.txt');
  assert.equal(spawn.outcome, 'spawnError');
  assert.equal(spawn.denied, undefined);
  assert.match(spawn.output, /ENOENT/);
});

test('cursor messages are rows and init names the model that ran', () => {
  const { rows } = normalize(fixture('cursor-basic.jsonl'), 'cursor');
  assert.deepEqual(rows.filter(r => r.kind === 'agent').map(r => r.text),
    ["I'll fix the typo first.", 'Fixed the typo; checks pass. curl was refused.']);
  assert.equal(rows.find(r => r.kind === 'user').text, 'Fix the typo and run the checks.');
  const life = rows.filter(r => r.kind === 'lifecycle');
  assert.deepEqual(life.map(r => r.label), ['session start', 'result']);
  assert.equal(life[0].detail, 'GPT-5.4 Mini Low');
  assert.equal(life[1].usage.inputTokens, 130);
});

test('a dropped cursor log is inferred from tool_call or thinking, never from shared types', () => {
  const HVn = globalThis.window.HandoffViewer;
  const text = fixture('cursor-basic.jsonl');
  assert.equal(HVn.inferBackend(text), 'cursor');
  assert.deepEqual(normalize(text), normalize(text, 'cursor'));
  assert.equal(HVn.inferBackend('{"type":"thinking","subtype":"delta","text":"x"}'), 'cursor');
  // The stated limit: a log that crashed after init holds only shared types.
  const sharedOnly = text.split('\n')
    .filter(line => /^\{"type":"(system|user|assistant|result)"/.test(line)).join('\n');
  assert.equal(HVn.inferBackend(sharedOnly), null);
});

// --- Antigravity -----------------------------------------------------------
// Every line discriminates on `event`, not `type`. The fixtures hold real
// lines from the probe logs: antigravity-basic.jsonl is P3 with one view_file
// step from RO1, antigravity-lifecycle.jsonl holds RSAME's system_message,
// RO1's error_message and refused write, and RMODEL's command_result.

const agy = (name) => normalize(fixture(name), 'antigravity');

// The summary line (glyph, label, size) the page prints for each row.
function mountedSummaries(payload) {
  return mountedStream(payload)
    .filter(el => el.className === 'row')
    .map(el => ({ glyph: el.children[0].children[0].textContent,
                  label: el.children[0].children[1].textContent }));
}

test('the antigravity fixture renders one row per step, in source order', () => {
  const { rows } = agy('antigravity-basic.jsonl');
  assert.deepEqual(rows.map(r => r.kind),
    ['lifecycle', 'agent', 'file_change', 'agent', 'command', 'tool', 'lifecycle']);
  assert.deepEqual(rows.map(r => r.pos), [0, 2, 4, 6, 10, 12, 14]);
  assert.deepEqual(rows.map(r => r.pos), [...rows.map(r => r.pos)].sort((a, b) => a - b));
});

test('antigravity text_delta chunks join into one agent row per step', () => {
  const { rows } = agy('antigravity-basic.jsonl');
  assert.deepEqual(rows.filter(r => r.kind === 'agent').map(r => r.text), [
    'Step 1: Attempting to edit `tracked.txt` to contain `probe-edit`.\n',
    'Step 2: Attempting to run `bash check.sh`.\n',
  ]);
});

test('antigravity tool steps fold their ACTIVE and DONE halves into one row', () => {
  const { rows } = agy('antigravity-basic.jsonl');
  const shell = rows.find(r => r.kind === 'command');
  assert.equal(shell.command, 'bash check.sh');
  assert.equal(shell.output, 'CHECK_OK\r\n');
  assert.equal(shell.outcome, 'DONE');
  assert.equal(shell.is_error, false);
  assert.equal(shell.pos, 10, 'keeps the ACTIVE line position');
  assert.equal(shell.raw.started.step_update.state, 'ACTIVE');
  assert.equal(shell.raw.completed.step_update.state, 'DONE');
  const write = rows.find(r => r.kind === 'file_change');
  assert.deepEqual(write.changes, [{ path: '<repo>/tracked.txt', kind: 'update' }]);
  assert.equal(write.outcome, 'DONE');
  const read = rows.find(r => r.kind === 'tool');
  assert.equal(read.tool, 'view_file');
  assert.equal(read.target, '<repo>/tracked.txt');
  assert.equal(read.output, '2 lines, 9 bytes');
  assert.equal(read.outcome, 'DONE');
  assert.equal(rows.filter(r => r.kind === 'error').length, 0);
});

test('an antigravity tool step with no DONE or ERROR yet has no outcome', () => {
  // A live job: the shell step has logged ACTIVE and nothing after.
  const live = fixture('antigravity-basic.jsonl').split('\n').slice(0, 11).join('\n');
  const { rows } = normalize(live, 'antigravity');
  const shell = rows.find(r => r.kind === 'command');
  assert.equal(shell.command, 'bash check.sh');
  assert.equal(shell.outcome, undefined);
  assert.equal(shell.is_error, undefined);
  assert.equal(shell.output, '');
});

test('a dropped antigravity log is inferred from the event key on line one', () => {
  const HVn = globalThis.window.HandoffViewer;
  const text = fixture('antigravity-basic.jsonl');
  assert.equal(HVn.inferBackend(text.split('\n')[0]), 'antigravity');
  assert.equal(HVn.inferBackend(fixture('antigravity-lifecycle.jsonl')), 'antigravity');
  assert.equal(HVn.inferBackend('{"event":"result","result":{"status":"ERROR"}}'), 'antigravity');
  assert.deepEqual(normalize(text), normalize(text, 'antigravity'));
});

test('no other backend log is inferred as antigravity', () => {
  const HVn = globalThis.window.HandoffViewer;
  assert.equal(HVn.inferBackend(fixture('codex-basic.jsonl')), 'codex');
  assert.equal(HVn.inferBackend(fixture('copilot-basic.jsonl')), 'copilot');
  assert.equal(HVn.inferBackend(fixture('cursor-basic.jsonl')), 'cursor');
  assert.equal(HVn.inferBackend(fixture('claude-tools.jsonl')), null);
  // claude partial-message output has an `event` key too, holding an object.
  assert.equal(HVn.inferBackend('{"type":"stream_event","event":{"type":"message_start"}}'), null);
});

test('an antigravity user_input step produces no row', () => {
  for (const name of ['antigravity-basic.jsonl', 'antigravity-lifecycle.jsonl']) {
    const { rows } = agy(name);
    assert.equal(rows.filter(r => r.kind === 'user').length, 0, name);
    assert.ok(rows.every(r => JSON.stringify(r.raw).indexOf('user_input') === -1), name);
  }
});

test('a generated antigravity payload renders exactly one prompt row, from prompt_text', () => {
  assertOnePrompt({
    meta: { backend: 'antigravity' }, log_text: fixture('antigravity-basic.jsonl'),
    prompt_text: 'Edit tracked.txt and run the checks.\n',
  }, 'Edit tracked.txt and run the checks.');
});

test('an ERROR antigravity tool step shows its message and is not a file change', () => {
  const { rows } = agy('antigravity-lifecycle.jsonl');
  const refused = rows.find(r => r.tool === 'write_to_file');
  assert.equal(refused.kind, 'error');
  assert.equal(refused.outcome, 'ERROR');
  assert.equal(refused.is_error, true);
  assert.match(refused.text, /user denied permission for write_file\(<repo>\/new\.txt\)/);
  assert.equal(refused.output, refused.text);
  assert.equal(refused.denied, undefined, 'only denied_actions decides a denial');
  assert.equal(refused.pending_edit, undefined);
  assert.equal(refused.pos, 4, 'keeps the ACTIVE line position');
  const [shown] = mountedSummaries({
    meta: { backend: 'antigravity' },
    log_text: fixture('antigravity-lifecycle.jsonl').split('\n').slice(4, 6).join('\n') });
  assert.equal(shown.glyph, '!');
  assert.match(shown.label, /user denied permission/);
});

test('system_message, error_message, and command_result each become a lifecycle row', () => {
  const { rows } = agy('antigravity-lifecycle.jsonl');
  const life = rows.filter(r => r.kind === 'lifecycle');
  assert.deepEqual(life.map(r => r.label), [
    'session start', 'system message', 'error message', 'result', 'command result', 'result']);
  const command = life.find(r => r.label === 'command result');
  assert.equal(command.detail, '/model');
  assert.equal(command.raw.command.data.id, 'gemini-3.8-flash-high');
  assert.equal(life.find(r => r.label === 'error message').is_error, true);
  assert.equal(life.find(r => r.label === 'system message').is_error, undefined);
});

test('the antigravity init row names the model, cwd, and permission mode', () => {
  const [fresh] = agy('antigravity-basic.jsonl').rows;
  assert.equal(fresh.label, 'session start');
  assert.equal(fresh.detail, 'gemini-3.8-flash-low · <repo> · permission always-proceed');
  // A resumed turn records no model (RSAME).
  const [resumed] = agy('antigravity-lifecycle.jsonl').rows;
  assert.equal(resumed.detail, '<repo> · permission request-review');
});

test('the antigravity result row shows its status, its error, and its denied actions', () => {
  const life = agy('antigravity-lifecycle.jsonl').rows.filter(r => r.label === 'result');
  assert.equal(life[0].detail,
    'ERROR · error: API error (attempt 1): RESOURCE_EXHAUSTED (code 429): ' +
    'You have exhausted your capacity on this model. Resets in 0s. · denied: WriteToFile');
  assert.equal(life[0].is_error, true);
  assert.equal(life[0].usage.input_tokens, 25867);
  assert.equal(life[1].detail, 'SUCCESS');
  assert.equal(life[1].is_error, undefined);
  const ok = agy('antigravity-basic.jsonl').rows.find(r => r.label === 'result');
  assert.equal(ok.detail, 'SUCCESS');
  assert.equal(ok.usage.input_tokens, 24183);
  const shown = mountedSummaries({
    meta: { backend: 'antigravity' }, log_text: fixture('antigravity-lifecycle.jsonl') });
  const failed = shown.find(s => s.label.startsWith('result ERROR'));
  assert.equal(failed.glyph, '!');
  assert.match(failed.label, /denied: WriteToFile$/);
});

test('an antigravity read shows its file in the row summary', () => {
  const shown = mountedSummaries({
    meta: { backend: 'antigravity' }, log_text: fixture('antigravity-basic.jsonl') });
  assert.deepEqual(shown.map(s => s.label), [
    'session start gemini-3.8-flash-low · <repo> · permission always-proceed',
    'update <repo>/tracked.txt',
    'bash check.sh',
    'view_file <repo>/tracked.txt',
    'result SUCCESS',
  ]);
});

test('an antigravity step that carries only usage produces no agent row', () => {
  // RO1: agent steps that ended with usage and no text_delta.
  const line = '{"event":"step_update","step_update":{"conversation_id":"agy-1","step_index":2,' +
    '"state":"DONE","step_type":"agent_response","duration_seconds":29.8,' +
    '"usage":{"input_tokens":12683,"output_tokens":275,"thinking_tokens":189,"cache_read_tokens":0,"total_tokens":12958}}}';
  assert.deepEqual(normalize(line, 'antigravity').rows, []);
});

test('an unexpected antigravity shape is shown or skipped, never fatal', () => {
  const lines = [
    '{"event":"step_update","step_update":null}',
    '{"event":"step_update","step_update":"text"}',
    '{"event":"step_update","step_update":[1]}',
    '{"event":"step_update","step_update":{"step_index":"7","step_type":"agent_response","text_delta":"a"}}',
    '{"event":"step_update","step_update":{"step_index":"7","step_type":"agent_response","text_delta":"b"}}',
    '{"event":"step_update","step_update":{"step_index":8,"step_type":"tool","state":"DONE","tool_info":null}}',
    '{"event":"step_update","step_update":{"step_index":9,"step_type":"tool","state":"DONE","tool_info":{"parameters":"x","output":5,"error":"x"}}}',
    '{"event":"step_update","step_update":{"step_index":10,"step_type":"brand_new"}}',
    '{"event":"step_update","step_update":{"step_index":10,"step_type":"brand_new"}}',
    '{"event":"init","init":null}',
    '{"event":"result","result":{"status":"SUCCESS"}}',
    '{"event":"result","result":{"status":"SUCCESS","denied_actions":"WriteToFile","usage":"none"}}',
    '{"event":"result","result":{"status":"SUCCESS","denied_actions":[null,3,{},{"action":"write_file"}]}}',
    '{"event":"result","result":null}',
    '{"event":"command_result","command":"model"}',
    '{"event":"surprise"}',
    '{"status":"no event key"}',
  ].join('\n');
  const { rows, partial } = normalize(lines, 'antigravity');
  assert.equal(partial, false);
  assert.deepEqual(rows.map(r => r.pos), [0, 1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16]);
  // Nothing is folded across unrelated steps: a non-integer index stands alone.
  assert.deepEqual(rows.slice(0, 3).map(r => [r.kind, r.type]), [
    ['unknown', 'step_update'], ['unknown', 'step_update'], ['unknown', 'step_update']]);
  assert.deepEqual(rows.slice(3, 5).map(r => [r.kind, r.text]), [['agent', 'a'], ['agent', 'b']]);
  // A tool step with a broken payload is still one row, with no invented output.
  assert.deepEqual(rows.slice(5, 7).map(r => [r.kind, r.tool, r.output]), [
    ['tool', '', ''], ['tool', '', '']]);
  // A repeated unknown step shows once.
  assert.equal(rows.filter(r => r.type === 'brand_new').length, 1);
  assert.deepEqual(rows.filter(r => r.label === 'result').map(r => r.detail), [
    'SUCCESS', 'SUCCESS', 'SUCCESS · denied: write_file', '']);
  assert.deepEqual(rows.slice(-3).map(r => [r.kind, r.type, r.label]), [
    ['lifecycle', undefined, 'command result'], ['unknown', 'surprise', undefined],
    ['unknown', '', undefined]]);
});

test('a refused antigravity write that ended DONE is listed by the result, not the tool row', () => {
  // A refused write that ended DONE (P0) leaves nothing on the tool row; only
  // the result event lists it.
  const log = [
    '{"event":"step_update","step_update":{"step_index":1,"step_type":"tool","state":"DONE",' +
      '"tool_name":"write_to_file","tool_info":{"parameters":{"TargetFile":"<repo>/a.txt"}}}}',
    '{"event":"result","result":{"status":"SUCCESS","denied_actions":' +
      '[{"action":"write_file","display_name":"WriteToFile"}]}}',
  ].join('\n');
  const { rows } = normalize(log, 'antigravity');
  assert.equal(rows[0].kind, 'file_change');
  assert.equal(rows[0].denied, undefined);
  assert.equal(rows[1].detail, 'SUCCESS · denied: WriteToFile');
});

test('antigravity agent markdown reaches the page through the allowlisted renderer', () => {
  const hostile = '<img src=x onerror=alert(1)> and [x](javascript:alert(1))';
  const log = '{"event":"step_update","step_update":{"step_index":1,"step_type":"agent_response",' +
    `"state":"DONE","text_delta":${JSON.stringify(hostile)}}}`;
  const [article] = mountedStream({ meta: { backend: 'antigravity' }, log_text: log });
  assert.deepEqual(markupViolations(article.innerHTML), []);
  assert.match(article.innerHTML, /&lt;img src=x onerror=alert\(1\)&gt;/);
});
