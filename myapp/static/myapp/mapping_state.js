/*
 * Stato delle associazioni segnaposto -> colonne nella pagina "Map Data to HERO".
 *
 * Lo stato di un blocco e' un oggetto { segnaposto: valore }, con nomi e mai
 * indici: e' lo stesso formato inviato al server nel campo connections_<id> e
 * l'unica fonte da cui la pagina disegna le frecce. Il valore e':
 *   - una stringa, il nome di una colonna (clic sinistro);
 *   - un oggetto { mode: 'sum' | 'concat', columns: [...], separator: '' },
 *     una combinazione di colonne nell'ordine dato (clic destro).
 * Le funzioni sono pure e non toccano il DOM, cosi' da poter essere verificate
 * con Node.
 */
(function (root) {
  'use strict';

  var MODES = { sum: true, concat: true };

  function isCombo(value) {
    return !!value && typeof value === 'object' && !Array.isArray(value);
  }

  function cloneValue(value) {
    if (!isCombo(value)) return value;
    return {
      mode: value.mode,
      columns: (value.columns || []).slice(),
      separator: value.separator || ''
    };
  }

  function copy(state) {
    var out = {};
    Object.keys(state || {}).forEach(function (k) { out[k] = cloneValue(state[k]); });
    return out;
  }

  /* Colonne usate da un valore, nell'ordine: una per una colonna, n per una combinazione. */
  function columnsOf(value) {
    if (isCombo(value)) return (value.columns || []).slice();
    return value ? [value] : [];
  }

  /* Associazioni salvate, solo se riferite alla tabella indicata. */
  function forTable(saved, table) {
    if (!saved || !table || saved.table !== table) return {};
    return copy(saved.pairs);
  }

  /*
   * Tiene solo le coppie con un segnaposto del blocco e colonne esistenti. Una
   * combinazione vale solo intera: se manca una colonna, la si scarta.
   */
  function retain(state, columns, placeholders) {
    var cols = {}, vars = {}, out = {};
    (columns || []).forEach(function (c) { cols[c] = true; });
    (placeholders || []).forEach(function (v) { vars[v] = true; });
    Object.keys(state || {}).forEach(function (v) {
      if (!vars[v]) return;
      var value = state[v];
      if (isCombo(value)) {
        var used = columnsOf(value);
        if (MODES[value.mode] && used.length >= 2 && used.every(function (c) { return cols[c]; })) {
          out[v] = cloneValue(value);
        }
      } else if (cols[value]) {
        out[v] = value;
      }
    });
    return out;
  }

  /*
   * Clic sinistro: associa il segnaposto alla colonna, sostituendo qualunque
   * associazione precedente (anche una combinazione); se e' gia' associato a
   * quella colonna l'associazione viene tolta. Una colonna puo' servire piu'
   * segnaposti.
   */
  function toggle(state, placeholder, column) {
    var out = copy(state);
    if (out[placeholder] === column) delete out[placeholder];
    else out[placeholder] = column;
    return out;
  }

  /*
   * Clic destro: associa il segnaposto a una combinazione. Nessuna colonna
   * toglie l'associazione, una sola e' una colonna normale, da due in su e' una
   * combinazione. Il separatore vale solo per la concatenazione.
   */
  function setCombination(state, placeholder, mode, columns, separator) {
    var out = copy(state);
    var cols = (columns || []).slice();
    if (cols.length === 0) {
      delete out[placeholder];
    } else if (cols.length === 1) {
      out[placeholder] = cols[0];
    } else {
      var m = MODES[mode] ? mode : 'concat';
      out[placeholder] = {
        mode: m,
        columns: cols,
        separator: m === 'concat' ? (separator || '') : ''
      };
    }
    return out;
  }

  /* Aggiunge o toglie una colonna dalla selezione, mantenendo l'ordine dei clic. */
  function pick(order, column) {
    var out = (order || []).slice();
    var i = out.indexOf(column);
    if (i === -1) out.push(column); else out.splice(i, 1);
    return out;
  }

  /* Colonne selezionabili per la modalita': la somma ammette solo le numeriche. */
  function usable(mode, columns, numeric) {
    if (mode !== 'sum') return (columns || []).slice();
    var ok = {};
    (numeric || []).forEach(function (c) { ok[c] = true; });
    return (columns || []).filter(function (c) { return ok[c]; });
  }

  /* Passando alla somma si tolgono dalla selezione le colonne non numeriche. */
  function restrict(mode, order, numeric) {
    if (mode !== 'sum') return (order || []).slice();
    var ok = {};
    (numeric || []).forEach(function (c) { ok[c] = true; });
    return (order || []).filter(function (c) { return ok[c]; });
  }

  /* Testo breve di un valore, per i suggerimenti: "a + b" (somma) o "a & b". */
  function describe(value) {
    if (!isCombo(value)) return value || '';
    var glue = value.mode === 'sum' ? ' + ' : ' & ';
    return columnsOf(value).join(glue);
  }

  /* Valore del campo nascosto: sempre JSON valido, mai stringa vuota. */
  function serialize(state) {
    return JSON.stringify(copy(state));
  }

  function parse(text) {
    try {
      var v = JSON.parse(text || '{}');
      return (v && typeof v === 'object' && !Array.isArray(v)) ? v : {};
    } catch (e) {
      return {};
    }
  }

  var api = {
    isCombo: isCombo, columnsOf: columnsOf, forTable: forTable, retain: retain,
    toggle: toggle, setCombination: setCombination, pick: pick, usable: usable,
    restrict: restrict, describe: describe, serialize: serialize, parse: parse
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.HdnMappingState = api;
})(this);
