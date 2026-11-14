// Ontology Monitor: ricerca e ispezione dei termini dell'ontologia attiva.
// L'indice arriva dal template (json_script "ontology-index"); il diagramma
// Mermaid viene chiesto al server per il termine selezionato.
(function () {
  var data = JSON.parse(document.getElementById('ontology-index').textContent);
  var graphUrl = document.getElementById('ontology-explorer').getAttribute('data-graph-url');
  var terms = data.terms;
  var byIri = {};
  terms.forEach(function (t) { byIri[t.iri] = t; });

  var KIND_NAMES = {
    class: 'Class',
    object_property: 'Object property',
    datatype_property: 'Datatype property',
    annotation_property: 'Annotation property',
    property: 'Property',
    individual: 'Individual'
  };
  // Un colore Bootstrap per tipo: badge, pulsanti e (lato server) nodi Mermaid.
  var KIND_COLOR = {
    class: 'warning',
    object_property: 'primary',
    datatype_property: 'success',
    annotation_property: 'info',
    property: 'dark',
    individual: 'purple'
  };
  var MAX_RESULTS = 300;
  var MAX_CHIPS = 60;

  var searchEl = document.getElementById('ontSearch');
  var kindEl = document.getElementById('ontKind');
  var countEl = document.getElementById('ontCount');
  var resultsEl = document.getElementById('ontResults');
  var detailEl = document.getElementById('ontDetail');
  var diagramEl = document.getElementById('ontDiagram');
  var noteEl = document.getElementById('ontDiagramNote');
  var findEl = document.getElementById('ontFind');
  var domainEl = document.getElementById('ontDomain');
  var rangeEl = document.getElementById('ontRange');
  var applyEl = document.getElementById('ontFindApply');
  var clearEl = document.getElementById('ontFindClear');
  var findStatusEl = document.getElementById('ontFindStatus');

  var current = null;
  var renderSeq = 0;
  var finder = null; // {domainLabel, domain, rangeLabel, range} quando la ricerca per domain/range e' attiva

  mermaid.initialize({ startOnLoad: false, securityLevel: 'strict' });

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function labelOf(iri) {
    return byIri[iri] ? byIri[iri].label : iri.split(/[#/]/).pop();
  }

  function isProperty(t) {
    return t.kind.indexOf('property') !== -1;
  }

  function badgeClass(kind) {
    var color = KIND_COLOR[kind] || 'secondary';
    var text = (color === 'warning' || color === 'info') ? ' text-dark' : '';
    return 'badge bg-' + color + text;
  }

  function chipClass(kind) {
    return 'btn btn-sm btn-outline-' + (KIND_COLOR[kind] || 'secondary') + ' me-1 mb-1';
  }

  // Il termine e i suoi antenati (sottoclassi -> superclassi): un dominio o un range
  // dichiarato su una superclasse vale anche per le sue sottoclassi.
  function selfAndAncestors(iri) {
    var out = {};
    var stack = [iri];
    while (stack.length) {
      var x = stack.pop();
      if (out[x]) continue;
      out[x] = true;
      var t = byIri[x];
      if (t) t.parents.forEach(function (p) { stack.push(p); });
    }
    return out;
  }

  function matchesFinder(t, f) {
    if (!isProperty(t)) return false;
    var domainOk = t.domain.some(function (d) { return f.domainSet[d]; });
    if (!domainOk) return false;
    if (!f.rangeSet) return true;
    return t.range.some(function (r) { return f.rangeSet[r]; });
  }

  function matchesText(t, q, kind) {
    if (kind && t.kind !== kind) return false;
    if (!q) return true;
    return (t.label + ' ' + t.iri).toLowerCase().indexOf(q) !== -1;
  }

  function renderResults() {
    var q = searchEl.value.trim().toLowerCase();
    var base = finder ? terms.filter(function (t) { return matchesFinder(t, finder); }) : terms;
    var list = base.filter(function (t) { return matchesText(t, q, kindEl.value); });
    countEl.textContent = list.length + ' of ' + base.length + ' terms';
    resultsEl.textContent = '';
    list.slice(0, MAX_RESULTS).forEach(function (t) {
      var b = el('button', 'list-group-item list-group-item-action py-1 small');
      b.type = 'button';
      b.title = t.iri;
      b.appendChild(el('span', '', t.label));
      b.appendChild(el('span', badgeClass(t.kind) + ' ms-2', KIND_NAMES[t.kind] || t.kind));
      if (current && current.iri === t.iri) b.classList.add('active');
      b.addEventListener('click', function () { select(t.iri); });
      resultsEl.appendChild(b);
    });
    if (list.length > MAX_RESULTS) {
      resultsEl.appendChild(el('div', 'small text-muted p-2', 'Refine the search to see more terms.'));
    }
  }

  function chips(title, iris) {
    if (!iris || !iris.length) return null;
    var wrap = el('div', 'mb-2');
    wrap.appendChild(el('div', 'small fw-semibold', title + ' (' + iris.length + ')'));
    iris.slice(0, MAX_CHIPS).forEach(function (iri) {
      if (byIri[iri]) {
        var b = el('button', chipClass(byIri[iri].kind), labelOf(iri));
        b.type = 'button';
        b.title = iri;
        b.addEventListener('click', function () { select(iri); });
        wrap.appendChild(b);
      } else {
        var s = el('span', 'badge bg-light text-dark border me-1 mb-1', labelOf(iri));
        s.title = iri;
        wrap.appendChild(s);
      }
    });
    if (iris.length > MAX_CHIPS) {
      wrap.appendChild(el('span', 'small text-muted', '… and ' + (iris.length - MAX_CHIPS) + ' more'));
    }
    return wrap;
  }

  function renderDetail() {
    detailEl.textContent = '';
    var t = current;
    var head = el('div', 'd-flex align-items-center gap-2 flex-wrap');
    head.appendChild(el('h5', 'mb-0', t.label));
    head.appendChild(el('span', badgeClass(t.kind), KIND_NAMES[t.kind] || t.kind));
    detailEl.appendChild(head);
    var iriLine = el('code', 'small d-block text-break mb-2', t.iri);
    detailEl.appendChild(iriLine);
    if (t.comment) detailEl.appendChild(el('p', 'small', t.comment));

    var sections = [];
    if (isProperty(t)) {
      sections.push(chips('Super-properties', t.parents));
      sections.push(chips('Sub-properties', t.children));
      sections.push(chips('Domain', t.domain));
      sections.push(chips('Range', t.range));
    } else if (t.kind === 'individual') {
      sections.push(chips('Types', t.types));
    } else {
      sections.push(chips('Superclasses', t.parents));
      sections.push(chips('Subclasses', t.children));
      sections.push(chips('Instances', t.instances));
    }
    sections.forEach(function (s) { if (s) detailEl.appendChild(s); });

    var copyBtn = el('button', 'btn btn-sm btn-link p-0 small', 'Copy IRI');
    copyBtn.type = 'button';
    copyBtn.addEventListener('click', function () {
      if (navigator.clipboard) navigator.clipboard.writeText(t.iri);
    });
    detailEl.appendChild(copyBtn);
  }

  function renderDiagram() {
    var seq = ++renderSeq;
    diagramEl.textContent = 'Rendering diagram…';
    noteEl.textContent = '';
    fetch(graphUrl + '?iri=' + encodeURIComponent(current.iri), { credentials: 'same-origin' })
      .then(function (r) {
        return r.json().then(function (j) { return { ok: r.ok, body: j }; });
      })
      .then(function (res) {
        if (seq !== renderSeq) return null;
        if (!res.ok) throw new Error(res.body.error || 'request failed');
        return mermaid.render('ontDiagramSvg' + seq, res.body.mermaid).then(function (out) {
          if (seq !== renderSeq) return;
          diagramEl.innerHTML = out.svg;
          noteEl.textContent = res.body.truncated
            ? 'Only the first nodes are shown; search for a narrower term to see the rest.' : '';
        });
      })
      .catch(function (err) {
        if (seq === renderSeq) {
          diagramEl.textContent = 'Diagram not available: ' + err.message;
        }
      });
  }

  function select(iri) {
    current = byIri[iri];
    if (!current) return;
    renderDetail();
    renderDiagram();
    renderResults();
  }

  // --- ricerca proprieta' per domain (obbligatorio) e range (facoltativo) ---

  function fillSelects() {
    var classes = terms.filter(function (t) { return t.kind === 'class'; });
    classes.sort(function (a, b) { return a.label.localeCompare(b.label); });
    var rangeIris = {};
    classes.forEach(function (c) { rangeIris[c.iri] = true; });
    terms.forEach(function (t) {
      if (isProperty(t)) t.range.forEach(function (r) { rangeIris[r] = true; });
    });

    domainEl.appendChild(el('option', '', '— choose a domain —'));
    domainEl.firstChild.value = '';
    classes.forEach(function (c) {
      var o = el('option', '', c.label);
      o.value = c.iri;
      domainEl.appendChild(o);
    });

    rangeEl.appendChild(el('option', '', 'Any range'));
    rangeEl.firstChild.value = '';
    Object.keys(rangeIris).map(function (iri) {
      return { iri: iri, label: labelOf(iri) };
    }).sort(function (a, b) { return a.label.localeCompare(b.label); }).forEach(function (r) {
      var o = el('option', '', r.label);
      o.value = r.iri;
      rangeEl.appendChild(o);
    });
  }

  function updateApplyState() {
    applyEl.disabled = !domainEl.value;
  }

  function applyFinder(event) {
    if (event) event.preventDefault();
    if (!domainEl.value) return;
    finder = {
      domain: domainEl.value,
      domainSet: selfAndAncestors(domainEl.value),
      range: rangeEl.value || null,
      rangeSet: rangeEl.value ? selfAndAncestors(rangeEl.value) : null
    };
    var parts = ['domain ' + labelOf(finder.domain) + ' (or superclass)'];
    if (finder.range) parts.push('range ' + labelOf(finder.range) + ' (or subclass)');
    var count = terms.filter(function (t) { return matchesFinder(t, finder); }).length;
    findStatusEl.textContent = count + ' properties with ' + parts.join(' and ') + '.';
    renderResults();
  }

  function clearFinder() {
    finder = null;
    domainEl.value = '';
    rangeEl.value = '';
    updateApplyState();
    findStatusEl.textContent = '';
    renderResults();
  }

  searchEl.addEventListener('input', renderResults);
  kindEl.addEventListener('change', renderResults);
  domainEl.addEventListener('change', updateApplyState);
  findEl.addEventListener('submit', applyFinder);
  clearEl.addEventListener('click', clearFinder);

  fillSelects();
  updateApplyState();
  renderResults();
  var first = terms.find(function (t) { return t.kind === 'class'; }) || terms[0];
  if (first) select(first.iri);
})();
