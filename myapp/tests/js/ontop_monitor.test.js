// Test di myapp/static/myapp/ontop_monitor.js. Eseguito da test_ontop_monitor.py.
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const M = require(path.join(__dirname, '..', '..', 'static', 'myapp', 'ontop_monitor.js'));

// Orologio finto: schedule registra i timer, run() li esegue in ordine.
function fakeClock() {
  const timers = [];
  return {
    timers,
    schedule(f, ms) { timers.push({ f, ms }); return timers.length; },
    cancel() {},
    async next() { const t = timers.shift(); assert.ok(t, 'nessun timer'); await t.f(); return t.ms; },
  };
}

const cases = {
  async 'riprogramma solo dopo la fine della richiesta precedente'() {
    const clock = fakeClock();
    let inFlight = 0, maxInFlight = 0;
    const p = M.createPoller({
      interval: 1000, schedule: clock.schedule, cancel: clock.cancel,
      fn: async () => { inFlight++; maxInFlight = Math.max(maxInFlight, inFlight);
                        await new Promise(r => setImmediate(r)); inFlight--; },
    });
    await p.start();
    assert.equal(clock.timers.length, 1);
    await clock.next(); await clock.next();
    assert.equal(maxInFlight, 1, 'richieste accavallate');
  },
  async 'backoff esponenziale sugli errori, con tetto'() {
    const clock = fakeClock();
    const errors = [];
    const p = M.createPoller({
      interval: 1000, maxInterval: 8000, schedule: clock.schedule, cancel: clock.cancel,
      fn: () => Promise.reject(new Error('down')), onError: (e, n) => errors.push(n),
    });
    await p.start();
    const delays = [clock.timers[0].ms];
    for (let i = 0; i < 4; i++) { await clock.next(); delays.push(clock.timers[0].ms); }
    assert.deepEqual(delays, [2000, 4000, 8000, 8000, 8000]);
    assert.deepEqual(errors, [1, 2, 3, 4, 5]);
  },
  async 'torna all intervallo normale al primo successo'() {
    const clock = fakeClock();
    let fail = true, recovered = 0;
    const p = M.createPoller({
      interval: 1000, schedule: clock.schedule, cancel: clock.cancel,
      fn: () => fail ? Promise.reject(new Error('x')) : Promise.resolve(),
      onRecover: () => recovered++,
    });
    await p.start(); await clock.next();
    assert.equal(clock.timers[0].ms, 4000);
    fail = false; await clock.next();
    assert.equal(clock.timers[0].ms, 1000);
    assert.equal(recovered, 1);
  },
  async 'stop ferma il polling'() {
    const clock = fakeClock();
    let calls = 0;
    const p = M.createPoller({ interval: 1000, schedule: clock.schedule, cancel: clock.cancel,
                               fn: async () => { calls++; } });
    await p.start(); p.stop(); await clock.next();
    assert.equal(calls, 1);
    assert.equal(clock.timers.length, 0);
  },
  'il log segue solo se si era gia in fondo'() {
    const el = { scrollHeight: 1000, clientHeight: 200, scrollTop: 800 };
    M.stickToBottom(el, () => { el.scrollHeight = 1500; });
    assert.equal(el.scrollTop, 1500);
    el.scrollTop = 100;
    M.stickToBottom(el, () => { el.scrollHeight = 2000; });
    assert.equal(el.scrollTop, 100, 'lo scroll dell utente e stato forzato');
  },
  async 'getJson e postAction rifiutano su errore HTTP'() {
    const bad = async () => ({ ok: false, status: 500, json: async () => ({}) });
    await assert.rejects(M.getJson(bad, '/x'), /HTTP 500/);
    await assert.rejects(M.postAction(bad, '/x', 't', 'start'), /HTTP 500/);
    let seen;
    const good = async (url, init) => { seen = init; return { ok: true, status: 200 }; };
    await M.postAction(good, '/x', 'tok', 'stop');
    assert.equal(seen.headers['X-CSRFToken'], 'tok');
    assert.equal(seen.body, 'action=stop');
  },
};

(async () => {
  let failed = 0;
  for (const [name, fn] of Object.entries(cases)) {
    try { await fn(); console.log('ok   ' + name); }
    catch (e) { failed++; console.log('FAIL ' + name + '\n     ' + e.message); }
  }
  console.log(`${Object.keys(cases).length - failed}/${Object.keys(cases).length} passati`);
  process.exit(failed ? 1 : 0);
})();
