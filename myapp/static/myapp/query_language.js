// Linguaggi SQL (DuckDB) e SPARQL: tokenizzazione, evidenziazione e suggerimenti.
// Funzioni pure, senza DOM: usate da query_editor.js e testate con node.
(function (root) {
  'use strict';

  var SQL_KEYWORDS = [
    'SELECT', 'FROM', 'WHERE', 'GROUP', 'BY', 'ORDER', 'HAVING', 'LIMIT', 'OFFSET', 'JOIN', 'LEFT',
    'RIGHT', 'INNER', 'OUTER', 'FULL', 'CROSS', 'ON', 'USING', 'AS', 'AND', 'OR', 'NOT', 'IN', 'IS',
    'NULL', 'LIKE', 'ILIKE', 'BETWEEN', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'DISTINCT', 'UNION',
    'ALL', 'EXCEPT', 'INTERSECT', 'WITH', 'INSERT', 'INTO', 'VALUES', 'UPDATE', 'SET', 'DELETE',
    'CREATE', 'TABLE', 'VIEW', 'DROP', 'ALTER', 'DESC', 'ASC', 'EXISTS', 'TRUE', 'FALSE', 'CAST',
    'INTERVAL', 'QUALIFY', 'WINDOW', 'OVER', 'PARTITION', 'PRIMARY', 'KEY', 'DEFAULT', 'IF',
    'REPLACE', 'EXPLAIN', 'PRAGMA', 'DESCRIBE', 'SHOW', 'NULLS', 'FIRST', 'LAST'
  ];
  var SQL_FUNCTIONS = [
    'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'COALESCE', 'NULLIF', 'LOWER', 'UPPER', 'LENGTH',
    'SUBSTRING', 'TRIM', 'ROUND', 'ABS', 'DATE_TRUNC', 'EXTRACT', 'STRFTIME', 'STRING_AGG',
    'LIST', 'ARRAY_AGG', 'REGEXP_MATCHES', 'REPLACE_ALL', 'CONCAT', 'NOW', 'CURRENT_DATE'
  ];
  var SPARQL_KEYWORDS = [
    'PREFIX', 'BASE', 'SELECT', 'CONSTRUCT', 'DESCRIBE', 'ASK', 'DISTINCT', 'REDUCED', 'WHERE',
    'FROM', 'NAMED', 'GRAPH', 'OPTIONAL', 'UNION', 'MINUS', 'FILTER', 'BIND', 'AS', 'VALUES',
    'ORDER', 'BY', 'ASC', 'DESC', 'LIMIT', 'OFFSET', 'GROUP', 'HAVING', 'SERVICE', 'SILENT', 'NOT',
    'IN', 'EXISTS', 'AND', 'OR', 'TRUE', 'FALSE', 'A', 'UNDEF'
  ];
  var SPARQL_FUNCTIONS = [
    'STR', 'LANG', 'DATATYPE', 'BOUND', 'IRI', 'URI', 'BNODE', 'COALESCE', 'IF', 'REGEX', 'REPLACE',
    'CONTAINS', 'STRSTARTS', 'STRENDS', 'STRLEN', 'UCASE', 'LCASE', 'SUBSTR', 'ISIRI', 'ISURI',
    'ISLITERAL', 'ISBLANK', 'ISNUMERIC', 'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'SAMPLE', 'GROUP_CONCAT'
  ];

  // Ogni regola e' sticky ('y'): si applica solo alla posizione corrente.
  var RULES = {
    sql: [
      ['comment', /--[^\n]*|\/\*[\s\S]*?(?:\*\/|$)/y],
      ['string', /'(?:[^']|'')*'?/y],
      ['quoted', /"(?:[^"]|"")*"?/y],
      ['number', /\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/y],
      ['word', /[A-Za-z_][A-Za-z0-9_$]*/y],
      ['ws', /\s+/y],
      ['op', /[\s\S]/y]
    ],
    sparql: [
      ['comment', /#[^\n]*/y],
      ['string', /"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)/y],
      ['string', /"(?:[^"\\\n]|\\.)*"?|'(?:[^'\\\n]|\\.)*'?/y],
      ['iri', /<[^<>"{}|^`\\\s]*>/y],
      ['variable', /[?$][A-Za-z0-9_]+/y],
      ['langtag', /@[A-Za-z]+(?:-[A-Za-z0-9]+)*/y],
      ['number', /\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/y],
      ['pname', /(?:[A-Za-z][\w-]*)?:[\w.\-%]*/y],
      ['word', /[A-Za-z_][\w-]*/y],
      ['ws', /\s+/y],
      ['op', /[\s\S]/y]
    ]
  };

  var TYPE_KEYWORDS = { sql: SQL_KEYWORDS, sparql: SPARQL_KEYWORDS };
  var TYPE_FUNCTIONS = { sql: SQL_FUNCTIONS, sparql: SPARQL_FUNCTIONS };

  function rulesFor(lang) {
    if (!RULES[lang]) throw new Error('unknown query language: ' + lang);
    return RULES[lang];
  }

  function nextNonSpace(text, pos) {
    var m = /\S/y;
    while (pos < text.length) {
      m.lastIndex = pos;
      if (m.test(text)) return text.charAt(pos);
      pos++;
    }
    return '';
  }

  // Lista di {type, text, start}; la concatenazione dei text e' sempre il testo originale.
  function tokenize(text, lang) {
    var rules = rulesFor(lang);
    var keywords = TYPE_KEYWORDS[lang];
    var functions = TYPE_FUNCTIONS[lang];
    var tokens = [];
    var pos = 0;
    while (pos < text.length) {
      for (var i = 0; i < rules.length; i++) {
        var re = rules[i][1];
        re.lastIndex = pos;
        var m = re.exec(text);
        if (m && m[0].length > 0) {
          var value = m[0];
          var type = rules[i][0];
          if (type === 'word') {
            var upper = value.toUpperCase();
            if (keywords.indexOf(upper) !== -1) type = 'keyword';
            else if (functions.indexOf(upper) !== -1 && nextNonSpace(text, pos + value.length) === '(') type = 'function';
            else type = 'ident';
          }
          tokens.push({ type: type, text: value, start: pos });
          pos += value.length;
          break;
        }
      }
    }
    return tokens;
  }

  function escapeHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // HTML per l'overlay dell'editor. Lo spazio finale evita che l'ultima riga sparisca.
  function highlightHtml(text, lang) {
    var out = tokenize(text, lang).map(function (t) {
      var esc = escapeHtml(t.text);
      if (t.type === 'ws' || t.type === 'op' || t.type === 'ident') return esc;
      return '<span class="hdn-tok-' + t.type + '">' + esc + '</span>';
    }).join('');
    return out + ' ';
  }

  // Nome prefissato (curie) per un IRI, usando il namespace piu' lungo che lo contiene.
  function curieFor(iri, prefixes) {
    var best = null;
    var bestNs = '';
    Object.keys(prefixes || {}).forEach(function (p) {
      var ns = prefixes[p];
      if (ns && iri.indexOf(ns) === 0 && ns.length > bestNs.length) {
        var local = iri.slice(ns.length);
        if (/^[A-Za-z_][\w.\-]*$/.test(local)) {
          best = p + ':' + local;
          bestNs = ns;
        }
      }
    });
    return best;
  }

  function rank(items, partial) {
    var p = partial.toLowerCase();
    var starts = [];
    var contains = [];
    items.forEach(function (it) {
      var l = it.label.toLowerCase();
      if (!p || l.indexOf(p) === 0) starts.push(it);
      else if (p.length >= 3 && l.indexOf(p) !== -1) contains.push(it);
    });
    return starts.concat(contains).slice(0, 40);
  }

  function sqlTables(schema) {
    return (schema && schema.tables) || {};
  }

  function findTable(schema, name) {
    var tables = sqlTables(schema);
    var key = Object.keys(tables).find(function (t) { return t.toLowerCase() === name.toLowerCase(); });
    return key === undefined ? null : key;
  }

  function sqlSuggest(text, caret, schema, manual) {
    var before = text.slice(0, caret);
    var m = before.match(/[\w$"]*(?:\.[\w$"]*)?$/);
    var word = m ? m[0] : '';
    var start = caret - word.length;
    var tables = sqlTables(schema);
    var prevKeyword = ((before.slice(0, start).match(/([A-Za-z_]+)\s*$/) || [])[1] || '').toUpperCase();

    if (word.indexOf('.') !== -1) {
      var dot = word.lastIndexOf('.');
      var qualifier = word.slice(0, dot).replace(/"/g, '');
      var partial = word.slice(dot + 1).replace(/"/g, '');
      var table = findTable(schema, qualifier);
      if (!table) return null;
      var cols = tables[table].map(function (c) { return { label: c, insert: c, kind: 'column', detail: table }; });
      var items = rank(cols, partial);
      return items.length ? { from: caret - partial.length, to: caret, items: items } : null;
    }

    var tableContext = ['FROM', 'JOIN', 'INTO', 'UPDATE', 'TABLE', 'VIEW', 'DESCRIBE'].indexOf(prevKeyword) !== -1;
    if (!manual && !tableContext && word.length < 2) return null;

    var tableItems = Object.keys(tables).map(function (t) {
      return { label: t, insert: t, kind: 'table', detail: tables[t].length + ' columns' };
    });
    var pool = tableItems;
    if (!tableContext) {
      // colonne delle tabelle gia' citate nella query, poi parole chiave e funzioni
      var referenced = {};
      var re = /\b(?:FROM|JOIN)\s+"?([A-Za-z_][\w$]*)"?/gi;
      var r;
      while ((r = re.exec(text)) !== null) {
        var t = findTable(schema, r[1]);
        if (t) referenced[t] = true;
      }
      Object.keys(referenced).forEach(function (t) {
        tables[t].forEach(function (c) {
          pool = pool.concat([{ label: c, insert: c, kind: 'column', detail: t }]);
        });
      });
      pool = pool.concat(SQL_KEYWORDS.map(function (k) {
        return { label: k, insert: k, kind: 'keyword', detail: '' };
      }), SQL_FUNCTIONS.map(function (f) {
        return { label: f, insert: f, kind: 'function', detail: '' };
      }));
    }
    var result = rank(pool, word.replace(/"/g, ''));
    return result.length ? { from: start, to: caret, items: result } : null;
  }

  function sparqlSuggest(text, caret, schema, manual) {
    var before = text.slice(0, caret);
    var m = before.match(/[?$\w\-:.]*$/);
    var word = m ? m[0] : '';
    var start = caret - word.length;
    var prefixes = (schema && schema.prefixes) || {};
    var terms = (schema && schema.terms) || [];

    if (word.charAt(0) === '?' || word.charAt(0) === '$') {
      var seen = {};
      var vars = [];
      var vre = /[?$][A-Za-z0-9_]+/g;
      var v;
      while ((v = vre.exec(text)) !== null) {
        if (!seen[v[0]]) { seen[v[0]] = true; vars.push({ label: v[0], insert: v[0], kind: 'variable', detail: '' }); }
      }
      var vitems = rank(vars, word);
      return vitems.length ? { from: start, to: caret, items: vitems } : null;
    }

    var colon = word.indexOf(':');
    if (colon !== -1) {
      var pfx = word.slice(0, colon);
      var local = word.slice(colon + 1);
      if (Object.prototype.hasOwnProperty.call(prefixes, pfx)) {
        var ns = prefixes[pfx];
        var locals = terms.filter(function (t) { return t.iri.indexOf(ns) === 0; }).map(function (t) {
          var name = t.iri.slice(ns.length);
          return { label: name, insert: name, kind: t.kind, detail: t.label };
        }).filter(function (it) { return /^[A-Za-z_][\w.\-]*$/.test(it.label); });
        var litems = rank(locals, local);
        return litems.length ? { from: caret - local.length, to: caret, items: litems } : null;
      }
    }

    if (!manual && word.length < 2) return null;

    var pool = SPARQL_KEYWORDS.map(function (k) {
      return { label: k, insert: k, kind: 'keyword', detail: '' };
    }).concat(SPARQL_FUNCTIONS.map(function (f) {
      return { label: f, insert: f, kind: 'function', detail: '' };
    }), Object.keys(prefixes).map(function (p) {
      return { label: p + ':', insert: p + ':', kind: 'prefix', detail: prefixes[p] };
    }), terms.map(function (t) {
      var curie = curieFor(t.iri, prefixes);
      return { label: t.label, insert: curie || ('<' + t.iri + '>'), kind: t.kind,
               detail: curie || t.iri };
    }));
    var res = rank(pool, word);
    return res.length ? { from: start, to: caret, items: res } : null;
  }

  // Suggerimenti per il testo prima del cursore. manual=true (Ctrl+Space) li mostra
  // anche per una sola lettera; altrimenti servono almeno due caratteri o un trigger.
  function suggest(text, caret, lang, schema, manual) {
    if (lang === 'sql') return sqlSuggest(text, caret, schema || {}, !!manual);
    if (lang === 'sparql') return sparqlSuggest(text, caret, schema || {}, !!manual);
    throw new Error('unknown query language: ' + lang);
  }

  var api = {
    tokenize: tokenize,
    highlightHtml: highlightHtml,
    suggest: suggest,
    curieFor: curieFor,
    SQL_KEYWORDS: SQL_KEYWORDS,
    SPARQL_KEYWORDS: SPARQL_KEYWORDS
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.HdnQueryLanguage = api;
})(typeof window !== 'undefined' ? window : globalThis);
