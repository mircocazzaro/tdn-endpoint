// Editor per le query: evidenziazione della sintassi e completamento semi-automatico.
// Una textarea trasparente sopra un overlay colorato; il popup dei suggerimenti segue il cursore.
// Tasti: digitando compaiono i suggerimenti; Frecce per scegliere, Invio/Tab per accettare,
// Esc per chiudere, Ctrl+Spazio per aprirli a richiesta, Ctrl+Invio per eseguire la query.
(function (root) {
  'use strict';

  var QL = root.HdnQueryLanguage;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  // Posizione del cursore relativa al bordo interno della textarea, misurata con uno
  // specchio che ha gli stessi stili e lo stesso testo prima del cursore.
  function caretCoords(ta, pos) {
    var style = window.getComputedStyle(ta);
    var mirror = el('div');
    [ 'boxSizing', 'width', 'fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing',
      'paddingTop', 'paddingLeft', 'paddingRight', 'paddingBottom', 'borderTopWidth',
      'borderLeftWidth', 'borderRightWidth', 'borderBottomWidth', 'tabSize' ].forEach(function (p) {
      mirror.style[p] = style[p];
    });
    mirror.style.position = 'absolute';
    mirror.style.visibility = 'hidden';
    mirror.style.top = '0';
    mirror.style.left = '-9999px';
    mirror.style.whiteSpace = 'pre-wrap';
    mirror.style.wordWrap = 'break-word';
    mirror.style.overflow = 'hidden';
    mirror.textContent = ta.value.slice(0, pos);
    var marker = el('span', '', ta.value.slice(pos) || '.');
    mirror.appendChild(marker);
    document.body.appendChild(mirror);
    var coords = { top: marker.offsetTop - ta.scrollTop, left: marker.offsetLeft - ta.scrollLeft,
                   line: parseFloat(style.lineHeight) || 20 };
    document.body.removeChild(mirror);
    return coords;
  }

  function attach(ta, schema, lang, options) {
    options = options || {};
    var wrap = el('div', 'hdn-editor');
    ta.parentNode.insertBefore(wrap, ta);
    wrap.appendChild(ta);
    ta.classList.remove('form-control');
    ta.classList.add('hdn-input');
    ta.setAttribute('spellcheck', 'false');
    ta.setAttribute('autocomplete', 'off');

    var overlay = el('pre', 'hdn-hl');
    overlay.setAttribute('aria-hidden', 'true');
    wrap.insertBefore(overlay, ta);

    var list = el('ul', 'hdn-suggest');
    list.style.display = 'none';
    wrap.appendChild(list);

    var state = { items: [], active: 0, from: 0, open: false };

    function paint() {
      overlay.innerHTML = QL.highlightHtml(ta.value, lang);
      overlay.scrollTop = ta.scrollTop;
      overlay.scrollLeft = ta.scrollLeft;
    }

    function close() {
      state.open = false;
      list.style.display = 'none';
      list.textContent = '';
    }

    function placeList() {
      var c = caretCoords(ta, ta.selectionStart);
      list.style.left = Math.max(0, c.left) + 'px';
      list.style.top = (c.top + c.line + 4) + 'px';
    }

    function render() {
      list.textContent = '';
      state.items.forEach(function (item, i) {
        var li = el('li', 'hdn-suggest-item' + (i === state.active ? ' active' : ''));
        li.appendChild(el('span', 'hdn-suggest-label', item.label));
        li.appendChild(el('span', 'hdn-kind hdn-kind-' + item.kind, item.kind));
        li.title = item.detail || '';
        li.addEventListener('mousedown', function (ev) {
          ev.preventDefault(); // il focus resta sulla textarea
          state.active = i;
          accept();
        });
        list.appendChild(li);
      });
      var activeEl = list.children[state.active];
      if (activeEl && activeEl.scrollIntoView) activeEl.scrollIntoView({ block: 'nearest' });
    }

    function refresh(manual) {
      var result = QL.suggest(ta.value, ta.selectionStart, lang, schema, manual);
      if (!result || !result.items.length) return close();
      state.items = result.items;
      state.from = result.from;
      state.active = 0;
      state.open = true;
      list.style.display = 'block';
      render();
      placeList();
    }

    function accept() {
      var item = state.items[state.active];
      if (!item) return close();
      var caret = ta.selectionStart;
      var value = ta.value;
      ta.value = value.slice(0, state.from) + item.insert + value.slice(caret);
      var pos = state.from + item.insert.length;
      ta.setSelectionRange(pos, pos);
      close();
      paint();
      ta.focus();
    }

    ta.addEventListener('input', function () {
      paint();
      refresh(false);
    });
    ta.addEventListener('scroll', function () {
      overlay.scrollTop = ta.scrollTop;
      overlay.scrollLeft = ta.scrollLeft;
      if (state.open) placeList();
    });
    ta.addEventListener('click', close);
    ta.addEventListener('blur', function () { setTimeout(close, 120); });
    ta.addEventListener('keydown', function (ev) {
      if (ev.ctrlKey && ev.key === ' ') {
        ev.preventDefault();
        refresh(true);
        return;
      }
      if (ev.ctrlKey && ev.key === 'Enter') {
        ev.preventDefault();
        if (ta.form) ta.form.requestSubmit ? ta.form.requestSubmit() : ta.form.submit();
        return;
      }
      if (!state.open) return;
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        var n = state.items.length;
        state.active = (state.active + (ev.key === 'ArrowDown' ? 1 : n - 1)) % n;
        render();
      } else if (ev.key === 'Enter' || ev.key === 'Tab') {
        ev.preventDefault();
        accept();
      } else if (ev.key === 'Escape') {
        ev.preventDefault();
        close();
      }
    });

    paint();
    if (options.autoFocus) ta.focus();
    return { paint: paint, close: close, refresh: refresh };
  }

  var api = { attach: attach, caretCoords: caretCoords };
  root.HdnQueryEditor = api;
})(typeof window !== 'undefined' ? window : globalThis);
