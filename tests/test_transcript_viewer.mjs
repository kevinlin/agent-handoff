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
