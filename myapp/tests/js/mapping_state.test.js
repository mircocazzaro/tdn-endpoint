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

  // --- combinazioni di colonne (clic destro) ---
  'setCombination crea una combinazione con le colonne nell ordine dato'() {
    const s = S.setCombination({}, 'name', 'concat', ['first', 'last'], ' ');
    assert.deepEqual(s, { name: { mode: 'concat', columns: ['first', 'last'], separator: ' ' } });
    assert.deepEqual(S.columnsOf(s.name), ['first', 'last']);
    assert.equal(S.isCombo(s.name), true);
  },
  'il separatore vale solo per la concatenazione'() {
    const s = S.setCombination({}, 'total', 'sum', ['a', 'b'], '-');
    assert.deepEqual(s.total, { mode: 'sum', columns: ['a', 'b'], separator: '' });
  },
  'una sola colonna e una colonna normale, nessuna toglie l associazione'() {
    let s = S.setCombination({}, 'x', 'sum', ['a'], '');
    assert.deepEqual(s, { x: 'a' });
    s = S.setCombination(s, 'x', 'sum', [], '');
    assert.deepEqual(s, {});
  },
  'una modalita sconosciuta ricade sulla concatenazione'() {
    const s = S.setCombination({}, 'x', 'avg', ['a', 'b'], '');
    assert.equal(s.x.mode, 'concat');
  },
  'il clic sinistro sostituisce una combinazione con la colonna scelta'() {
    let s = S.setCombination({}, 'x', 'sum', ['a', 'b'], '');
    s = S.toggle(s, 'x', 'a');
    assert.deepEqual(s, { x: 'a' }, 'una colonna usata nella combinazione non la mantiene');
    s = S.setCombination(s, 'x', 'concat', ['a', 'b'], '');
    assert.equal(S.isCombo(s.x), true);
  },
  'le operazioni non modificano lo stato di partenza'() {
    const s = S.setCombination({}, 'x', 'concat', ['a', 'b'], ' ');
    const before = JSON.stringify(s);
    S.setCombination(s, 'x', 'sum', ['c', 'd'], '');
    S.toggle(s, 'x', 'z');
    S.retain(s, ['a'], ['x']);
    assert.equal(JSON.stringify(s), before);
    const c = S.forTable({ table: 'T', pairs: s }, 'T');
    c.x.columns.push('zzz');
    assert.deepEqual(s.x.columns, ['a', 'b'], 'forTable copia anche le colonne');
  },
  'retain tiene una combinazione solo se esistono tutte le sue colonne'() {
    const s = S.setCombination({ p: 'pid' }, 'x', 'concat', ['a', 'b'], '');
    assert.deepEqual(S.retain(s, ['pid', 'a', 'b'], ['p', 'x']), s);
    assert.deepEqual(S.retain(s, ['pid', 'a'], ['p', 'x']), { p: 'pid' }, 'manca b');
    assert.deepEqual(S.retain(s, ['pid', 'a', 'b'], ['p']), { p: 'pid' }, 'segnaposto sconosciuto');
  },
  'retain scarta una combinazione malformata'() {
    const bad = { x: { mode: 'avg', columns: ['a', 'b'] }, y: { mode: 'sum', columns: ['a'] } };
    assert.deepEqual(S.retain(bad, ['a', 'b'], ['x', 'y']), {});
  },
  'pick mantiene l ordine dei clic e toglie al secondo'() {
    let o = S.pick([], 'b');
    o = S.pick(o, 'a');
    o = S.pick(o, 'c');
    assert.deepEqual(o, ['b', 'a', 'c']);
    assert.deepEqual(S.pick(o, 'a'), ['b', 'c']);
    assert.deepEqual(o, ['b', 'a', 'c'], 'non modifica l originale');
  },
  'la somma ammette solo le colonne numeriche'() {
    const cols = ['id', 'age', 'name', 'weight'];
    const numeric = ['age', 'weight'];
    assert.deepEqual(S.usable('sum', cols, numeric), ['age', 'weight']);
    assert.deepEqual(S.usable('concat', cols, numeric), cols);
    assert.deepEqual(S.restrict('sum', ['name', 'age', 'id', 'weight'], numeric), ['age', 'weight']);
    assert.deepEqual(S.restrict('concat', ['name', 'age'], numeric), ['name', 'age']);
  },
  'describe mostra le colonne con il simbolo della modalita'() {
    assert.equal(S.describe('pid'), 'pid');
    assert.equal(S.describe({ mode: 'sum', columns: ['a', 'b', 'c'] }), 'a + b + c');
    assert.equal(S.describe({ mode: 'concat', columns: ['a', 'b'] }), 'a & b');
    assert.equal(S.describe(undefined), '');
  },
  'serialize e parse conservano le combinazioni'() {
    const s = S.setCombination({ p: 'pid' }, 'x', 'concat', ['a', 'b'], ', ');
    assert.deepEqual(S.parse(S.serialize(s)), s);
  },
};

let failed = 0;
for (const [name, fn] of Object.entries(cases)) {
  try { fn(); console.log('ok   ' + name); }
  catch (e) { failed++; console.log('FAIL ' + name + '\n     ' + e.message); }
}
console.log(`${Object.keys(cases).length - failed}/${Object.keys(cases).length} passati`);
process.exit(failed ? 1 : 0);
