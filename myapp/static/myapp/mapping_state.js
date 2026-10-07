/*
 * Stato delle associazioni segnaposto -> colonna nella pagina "Map Data to HERO".
 *
 * Lo stato di un blocco e' un oggetto { segnaposto: colonna }, con nomi e mai
 * indici: e' lo stesso formato inviato al server nel campo connections_<id> e
 * l'unica fonte da cui la pagina disegna le frecce. Le funzioni sono pure e
 * non toccano il DOM, cosi' da poter essere verificate con Node.
 */
(function (root) {
  'use strict';

  function copy(state) {
    var out = {};
    Object.keys(state || {}).forEach(function (k) { out[k] = state[k]; });
    return out;
  }

  /* Associazioni salvate, solo se riferite alla tabella indicata. */
  function forTable(saved, table) {
    if (!saved || !table || saved.table !== table) return {};
    return copy(saved.pairs);
  }

  /* Tiene solo le coppie con un segnaposto del blocco e una colonna esistente. */
  function retain(state, columns, placeholders) {
    var cols = {}, vars = {}, out = {};
    (columns || []).forEach(function (c) { cols[c] = true; });
    (placeholders || []).forEach(function (v) { vars[v] = true; });
    Object.keys(state || {}).forEach(function (v) {
      if (vars[v] && cols[state[v]]) out[v] = state[v];
    });
    return out;
  }

  /*
   * Associa il segnaposto alla colonna; se e' gia' associato a quella colonna
   * l'associazione viene tolta. Una colonna puo' servire piu' segnaposti.
   */
  function toggle(state, placeholder, column) {
    var out = copy(state);
    if (out[placeholder] === column) delete out[placeholder];
    else out[placeholder] = column;
    return out;
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

  var api = { forTable: forTable, retain: retain, toggle: toggle,
              serialize: serialize, parse: parse };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.HdnMappingState = api;
})(this);
