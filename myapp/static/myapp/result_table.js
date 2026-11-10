// Tabelle dei risultati (table.hdn-results): ordinamento per colonna, filtro sulle righe.
// Cliccando un'intestazione: crescente, decrescente, poi ordine originale.
(function (root) {
  'use strict';

  var ARROWS = { 1: '▲', '-1': '▼' };

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function cellText(td) {
    return (td.textContent || '').trim();
  }

  // Confronto: numerico se entrambi i valori sono numeri, altrimenti testuale
  // con ordinamento naturale (es. "2" prima di "10"). I vuoti vanno sempre in fondo.
  function compare(a, b, dir) {
    if (a === '' && b === '') return 0;
    if (a === '') return 1;
    if (b === '') return -1;
    var na = Number(a);
    var nb = Number(b);
    if (isFinite(na) && isFinite(nb)) return (na - nb) * dir;
    return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' }) * dir;
  }

  function isNumericColumn(rows, col) {
    var seen = false;
    for (var i = 0; i < rows.length; i++) {
      var v = cellText(rows[i].cells[col] || { textContent: '' });
      if (v === '') continue;
      if (!isFinite(Number(v))) return false;
      seen = true;
    }
    return seen;
  }

  function enhance(table) {
    var tbody = table.tBodies[0];
    var head = table.tHead && table.tHead.rows[0];
    if (!tbody || !head) return;

    var allRows = Array.prototype.slice.call(tbody.rows);
    allRows.forEach(function (r, i) { r.dataset.order = String(i); });
    table.classList.add('hdn-results');
    if (table.parentNode) table.parentNode.classList.add('hdn-results-wrap');

    var state = { col: -1, dir: 0 };

    // barra con filtro e contatore, sopra la tabella
    var bar = el('div', 'hdn-results-tools');
    var filter = el('input', 'form-control form-control-sm');
    filter.type = 'search';
    filter.placeholder = 'Filter rows…';
    filter.setAttribute('aria-label', 'Filter result rows');
    var counter = el('span', 'small text-muted ms-auto');
    bar.appendChild(filter);
    bar.appendChild(counter);
    table.parentNode.parentNode.insertBefore(bar, table.parentNode);

    var headers = Array.prototype.slice.call(head.cells);
    var indicators = headers.map(function (th, c) {
      th.classList.add('hdn-sortable');
      th.setAttribute('role', 'button');
      th.setAttribute('tabindex', '0');
      th.title = 'Click to sort';
      var ind = el('span', 'hdn-sort-ind');
      th.appendChild(ind);
      th.addEventListener('click', function () { sortBy(c); });
      th.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); sortBy(c); }
      });
      return ind;
    });

    function updateHeaders() {
      headers.forEach(function (th, c) {
        var active = c === state.col && state.dir !== 0;
        th.classList.toggle('hdn-sorted', active);
        th.setAttribute('aria-sort', active ? (state.dir === 1 ? 'ascending' : 'descending') : 'none');
        indicators[c].textContent = active ? ARROWS[state.dir] : '';
      });
    }

    function applyOrder() {
      var rows = Array.prototype.slice.call(tbody.rows);
      if (state.dir === 0) {
        rows.sort(function (a, b) { return Number(a.dataset.order) - Number(b.dataset.order); });
      } else {
        var numeric = isNumericColumn(rows, state.col);
        rows.sort(function (a, b) {
          var av = cellText(a.cells[state.col] || { textContent: '' });
          var bv = cellText(b.cells[state.col] || { textContent: '' });
          if (numeric && av !== '' && bv !== '') {
            return (Number(av) - Number(bv)) * state.dir;
          }
          return compare(av, bv, state.dir);
        });
        // allineamento a destra per le colonne numeriche
        headers.forEach(function (th, c) {
          var right = isNumericColumn(rows, c);
          th.classList.toggle('text-end', right);
          rows.forEach(function (r) {
            if (r.cells[c]) r.cells[c].classList.toggle('text-end', right);
          });
        });
      }
      rows.forEach(function (r) { tbody.appendChild(r); });
      updateHeaders();
    }

    function sortBy(c) {
      if (state.col !== c) { state.col = c; state.dir = 1; }
      else if (state.dir === 1) { state.dir = -1; }
      else { state.col = -1; state.dir = 0; }
      applyOrder();
    }

    function applyFilter() {
      var q = filter.value.trim().toLowerCase();
      var total = 0;
      var shown = 0;
      Array.prototype.forEach.call(tbody.rows, function (r) {
        total++;
        var hit = !q || r.textContent.toLowerCase().indexOf(q) !== -1;
        r.hidden = !hit;
        if (hit) shown++;
      });
      counter.textContent = shown === total
        ? total + (total === 1 ? ' row' : ' rows')
        : shown + ' of ' + total + ' rows';
    }

    filter.addEventListener('input', applyFilter);
    applyFilter();
    updateHeaders();
  }

  function enhanceAll(root) {
    Array.prototype.forEach.call(root.querySelectorAll('table.hdn-results-source'), enhance);
  }

  var api = { enhance: enhance, enhanceAll: enhanceAll, compare: compare };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.HdnResultTable = api;
})(typeof window !== 'undefined' ? window : globalThis);
