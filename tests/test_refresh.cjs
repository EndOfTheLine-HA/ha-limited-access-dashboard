const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

test('polling keeps ordinary controls enabled and follows availability', async () => {
  let poll;
  let available = true;
  let disabled = false;
  const control = {
    dataset: {},
    classList: { toggle() {} },
    toggleAttribute(_name, force) {
      disabled = force === undefined ? !disabled : Boolean(force);
    },
  };
  const card = {
    classList: { toggle() {} },
    querySelector() { return null; },
    querySelectorAll() { return [control]; },
  };
  const grid = {
    dataset: { stateUrl: '/demo/api/state' },
    querySelectorAll() { return [card]; },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,
    '../custom_components/limited_access_dashboard/static/app.js'), 'utf8'), {
    document: { querySelector(selector) { return selector === '[data-state-url]' ? grid : null; } },
    CSS: { escape(value) { return value; } },
    window: { setInterval(callback) { poll = callback; }, dispatchEvent() {} },
    CustomEvent: class {},
    fetch: async () => ({ ok: true, json: async () => ({
      available: true,
      cards: [{ entity_id: 'light.demo', available, media_controls: [] }],
    }) }),
  });
  for (let i = 0; i < 4; i++) {
    await poll();
    assert.equal(disabled, false, 'an available light stays enabled across polls');
  }
  available = false;
  await poll();
  assert.equal(disabled, true);
  available = true;
  await poll();
  assert.equal(disabled, false);
});
