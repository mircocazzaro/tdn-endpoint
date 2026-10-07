// Test di myapp/static/myapp/mapping_state.js. Eseguito da test_mapping_ui.py.
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const S = require(path.join(__dirname, '..', '..', 'static', 'myapp', 'mapping_state.js'));

const cases = {
  'le associazioni salvate valgono solo per la loro tabella'() {
    const saved = { table: 'PATIENTS', pairs: { patient: 'pid', sex: 'SEX' } };
    assert.deepEqual(S.forTable(saved, 'PATIENTS'), { patient: 'pid', sex: 'SEX' });
    assert.deepEqual(S.forTable(saved, 'VISITS'), {});
    assert.deepEqual(S.forTable(saved, ''), {});
    assert.deepEqual(S.forTable(undefined, 'PATIENTS'), {});
  },
  'retain scarta colonne e segnaposti inesistenti'() {
    const state = { patient: 'pid', sex: 'gone', ghost: 'pid' };
    assert.deepEqual(S.retain(state, ['pid', 'SEX'], ['patient', 'sex']), { patient: 'pid' });
  },
  'toggle riassocia sostituendo, e una seconda volta toglie'() {
    let s = S.toggle({}, 'patient', 'pid');
    assert.deepEqual(s, { patient: 'pid' });
    s = S.toggle(s, 'patient', 'other');
    assert.deepEqual(s, { patient: 'other' }, 'un segnaposto ha una sola colonna');
    s = S.toggle(s, 'patient', 'other');
    assert.deepEqual(s, {});
  },
  'una colonna puo servire piu segnaposti'() {
    let s = S.toggle({}, 'a', 'pid');
    s = S.toggle(s, 'b', 'pid');
    assert.deepEqual(s, { a: 'pid', b: 'pid' });
  },
  'toggle non modifica lo stato di partenza'() {
    const s = { a: 'x' };
    S.toggle(s, 'a', 'y');
    assert.deepEqual(s, { a: 'x' });
  },
  'serialize produce sempre JSON con soli nomi'() {
    assert.equal(S.serialize({}), '{}');
    assert.equal(S.serialize(undefined), '{}');
    assert.deepEqual(JSON.parse(S.serialize({ patient: 'pid' })), { patient: 'pid' });
  },
  'parse tollera valori vuoti o malformati'() {
    assert.deepEqual(S.parse(''), {});
    assert.deepEqual(S.parse('non json'), {});
    assert.deepEqual(S.parse('[1,2]'), {});
    assert.deepEqual(S.parse('{"a":"b"}'), { a: 'b' });
  },
};

let failed = 0;
for (const [name, fn] of Object.entries(cases)) {
  try { fn(); console.log('ok   ' + name); }
  catch (e) { failed++; console.log('FAIL ' + name + '\n     ' + e.message); }
}
console.log(`${Object.keys(cases).length - failed}/${Object.keys(cases).length} passati`);
process.exit(failed ? 1 : 0);
