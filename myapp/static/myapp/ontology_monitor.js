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
  var MAX_RESULTS = 300;
  var MAX_CHIPS = 60;

  var searchEl = document.getElementById('ontSearch');
  var kindEl = document.getElementById('ontKind');
  var countEl = document.getElementById('ontCount');
  var resultsEl = document.getElementById('ontResults');
  var detailEl = document.getElementById('ontDetail');
  var diagramEl = document.getElementById('ontDiagram');
  var noteEl = document.getElementById('ontDiagramNote');

  var current = null;
  var renderSeq = 0;

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

  function matches(t, q, kind) {
    if (kind && t.kind !== kind) return false;
    if (!q) return true;
    return (t.label + ' ' + t.iri).toLowerCase().indexOf(q) !== -1;
  }

  function renderResults() {
    var q = searchEl.value.trim().toLowerCase();
    var list = terms.filter(function (t) { return matches(t, q, kindEl.value); });
    countEl.textContent = list.length + ' of ' + terms.length + ' terms';
    resultsEl.textContent = '';
    list.slice(0, MAX_RESULTS).forEach(function (t) {
      var b = el('button', 'list-group-item list-group-item-action py-1 small');
      b.type = 'button';
      b.title = t.iri;
      b.appendChild(el('span', '', t.label));
      b.appendChild(el('span', 'badge bg-secondary ms-2', KIND_NAMES[t.kind] || t.kind));
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
        var b = el('button', 'btn btn-sm btn-outline-secondary me-1 mb-1', labelOf(iri));
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
    head.appendChild(el('span', 'badge bg-primary', KIND_NAMES[t.kind] || t.kind));
    detailEl.appendChild(head);
    var iriLine = el('code', 'small d-block text-break mb-2', t.iri);
    detailEl.appendChild(iriLine);
    if (t.comment) detailEl.appendChild(el('p', 'small', t.comment));

    var isProperty = t.kind.indexOf('property') !== -1;
    var sections = [];
    if (isProperty) {
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

  searchEl.addEventListener('input', renderResults);
  kindEl.addEventListener('change', renderResults);

  renderResults();
  var first = terms.find(function (t) { return t.kind === 'class'; }) || terms[0];
  if (first) select(first.iri);
})();
