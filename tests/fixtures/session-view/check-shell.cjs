// Exercise the shipped shell script with a small DOM, without a browser dependency.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {html, payload} = JSON.parse(fs.readFileSync(0, 'utf8'));
class Element {
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.attrs = {}; this.listeners = {}; this.style = {}; this.hidden = false; this.classList = {toggle() {}}; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text || ''; }
  append(node) { this.children.push(node); }
  setAttribute(key, value) { this.attrs[key] = value; }
  getAttribute(key) { return this.attrs[key]; }
  set src(value) { this.attrs.src = value; }
  addEventListener(event, callback) { this.listeners[event] = callback; }
  focus() { this.focused = true; }
}
const elements = new Map();
const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
const tabs = ['overview', 'transcript', 'cost', 'facts', 'denials'].map(name => {
  const tab = get('tab-' + name); tab.id = 'tab-' + name; tab.setAttribute('aria-controls', name); return tab;
});
get('handoff-payload').textContent = JSON.stringify(payload);
const document = {getElementById: get, createElement: tag => new Element(tag), querySelectorAll: () => tabs};
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
assert.equal(scripts.length, 1);
vm.runInNewContext(scripts[0][1], {document, navigator: {clipboard: {writeText: async () => {}}}});
get('tab-cost').listeners.click();
assert.equal(get('cost-frame').attrs.src, payload.cost_url);
get('tab-transcript').listeners.click();
assert.equal(get('transcript-frame').attrs.src, payload.jobs[0].url);
assert.equal(get('transcript-frame').hidden, false);
get('job-select').value = payload.jobs[1].job_id;
get('job-select').listeners.change();
assert.equal(get('transcript-frame').attrs.src, payload.jobs[1].url);
get('tab-facts').listeners.keydown({key: 'ArrowRight', preventDefault() {}});
assert.equal(get('tab-denials').attrs['aria-selected'], 'true');
assert.equal(get('tab-denials').focused, true);
assert.equal(get('facts').hidden, true);
assert.equal(get('denials').hidden, false);
function visit(node, fn) { fn(node); for (const child of node.children) visit(child, fn); }
for (const element of elements.values()) visit(element, node => {
  assert.ok(!node.attrs.src || node.attrs.src.includes('?token=fixture-token'));
  assert.ok(!Object.keys(node.attrs).some(key => /^on/i.test(key)));
  assert.ok(!['img', 'svg', 'script'].includes(node.tag)); // All new data nodes are inert.
});
if (payload.overview.image) {
  assert.equal(get('timeline').attrs.src, payload.overview.image);
  assert.equal(get('diagram').hidden, false);
  assert.equal(get('toggle-wrap').hidden, !payload.overview.mapped);
  if (payload.overview.mapped) {
    const buttons = get('overlay').children;
    assert.equal(buttons.length, payload.overview.hotspots.length);
    buttons[0].listeners.click();
    assert.equal(get('tab-facts').attrs['aria-selected'], 'true');
    buttons.find((_, i) => payload.overview.hotspots[i].job === 'job-a').listeners.click();
    assert.equal(get('transcript-frame').attrs.src, payload.jobs[0].url);
  }
} else {
  assert.equal(get('generation').hidden, false);
  assert.equal(get('prompt').textContent, payload.prompt);
}
const goal = get('goal');
assert.equal(goal.children.some(node => node.tag === 'details'), !payload.goal.matched && !!payload.goal.raw);
if (!payload.goal.matched) assert.equal(goal.children[0].textContent, 'no goal file matches this receipt');
let hostileVisible = false;
for (const node of elements.values()) visit(node, child => { if (child.textContent.includes('<img onerror=bad>')) hostileVisible = true; });
assert.ok(hostileVisible);
console.log('Shell tabs, authenticated resources, hotspots, goal disclosure, and text-only evidence: OK');
