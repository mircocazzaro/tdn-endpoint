/*
 * Logica della pagina Ontop Monitor, senza dipendenze dal DOM reale.
 *
 * - createPoller: interroga a intervalli. Una richiesta parte solo dopo la
 *   fine della precedente; in caso di errore l'intervallo raddoppia fino a un
 *   massimo e torna normale al primo successo. Prima ogni setInterval
 *   continuava a sparare richieste ogni 2-3 s anche con il server giu', senza
 *   gestire l'errore, e le richieste potevano accavallarsi.
 * - stickToBottom: il log segue le nuove righe solo se l'utente era gia' in
 *   fondo, cosi' che si possa scorrere indietro a leggere.
 * - postAction: invia start/stop e rifiuta se il server risponde con errore,
 *   cosi' che la pagina possa ripristinare l'interruttore e segnalarlo.
 */
(function (root) {
  'use strict';

  function createPoller(opts) {
    var fn = opts.fn;
    var interval = opts.interval;
    var maxInterval = opts.maxInterval || interval * 16;
    var schedule = opts.schedule || function (f, ms) { return setTimeout(f, ms); };
    var cancel = opts.cancel || function (h) { clearTimeout(h); };
    var onError = opts.onError || function () {};
    var onRecover = opts.onRecover || function () {};
    var failures = 0, handle = null, running = false;

    function delay() {
      return failures === 0 ? interval : Math.min(interval * Math.pow(2, failures), maxInterval);
    }

    function tick() {
      handle = null;
      if (!running) return Promise.resolve();
      return Promise.resolve().then(fn).then(function () {
        if (failures > 0) onRecover();
        failures = 0;
      }, function (err) {
        failures += 1;
        onError(err, failures);
      }).then(function () {
        if (running) handle = schedule(tick, delay());
      });
    }

    return {
      start: function () { if (!running) { running = true; return tick(); } return Promise.resolve(); },
      stop: function () { running = false; if (handle !== null) { cancel(handle); handle = null; } },
      failures: function () { return failures; },
      nextDelay: delay
    };
  }

  function stickToBottom(el, update) {
    var atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 20;
    update();
    if (atBottom) el.scrollTop = el.scrollHeight;
  }

  function getJson(fetchFn, url) {
    return fetchFn(url, { credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function postAction(fetchFn, url, csrfToken, action) {
    return fetchFn(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrfToken, 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'action=' + encodeURIComponent(action)
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r;
    });
  }

  var api = { createPoller: createPoller, stickToBottom: stickToBottom,
              getJson: getJson, postAction: postAction };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.HdnOntopMonitor = api;
})(this);
