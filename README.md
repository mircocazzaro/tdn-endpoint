# HDN Endpoint

Nodo locale della Hereditary Data Network (HDN, deliverable D3.2 di HEREDITARY).
Ogni istituzione partecipante installa un endpoint accanto ai propri dati:

- carica i dati in un database locale (DuckDB), che resta nell'istituzione;
- li collega all'ontologia HERO tramite un mapping (Ontop, OBDA);
- risponde alle query di HDN Central solo se sono nel catalogo delle query
  ammesse e non superano il livello di disclosure scelto dall'istituzione.

HDN Central non vede mai i dati: riceve solo le risposte alle query ammesse.

---

## Prima di iniziare: limiti di sicurezza attuali

Questa versione **non va esposta su una rete non fidata**. In particolare:

- **l'interfaccia di amministrazione (porta 8000) non ha autenticazione**:
  chiunque la raggiunga puo' caricare o cancellare dati, cambiare il livello
  di disclosure ed eseguire SQL sui dati;
- **Ontop (porta 8080) accetta qualunque query SPARQL senza controlli**: il
  catalogo e i livelli di disclosure valgono solo sulla porta 8084;
- `DEBUG` e' attivo e `SECRET_KEY` e' quella del repository.

Fino a quando questi punti non sono risolti, rendete raggiungibile da HDN
Central **solo la porta 8084**, e le porte 8000 e 8080 solo dalla macchina
stessa (firewall, o un tunnel SSH per usare l'interfaccia da remoto).

---

## Requisiti

| | |
|---|---|
| Sistema | Linux (verificato su Ubuntu 24.04) |
| Python | 3.10 o successivo (installazione verificata con 3.12) |
| Java | 11 o successivo, per Ontop (verificato con OpenJDK 21) |
| Disco | circa 300 MB per il repository, piu' le dipendenze Python e i dati |
| Rete | serve solo per installare le dipendenze Python. In esercizio l'endpoint non accede a Internet: librerie dell'interfaccia e ontologie importate sono incluse nel repository |

Su Ubuntu/Debian:

```bash
sudo apt install python3 python3-venv default-jre-headless git
```

---

## Installazione

```bash
git clone https://github.com/mircocazzaro/tdn-endpoint.git
cd tdn-endpoint

python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
python manage.py collectstatic --noinput
```

Facoltativo, per verificare l'installazione (circa 15 secondi, tutti i test
devono passare):

```bash
python manage.py test myapp.tests
```

---

## Avvio

L'endpoint e' composto da tre processi:

| porta | processo | chi lo usa |
|---|---|---|
| 8000 | interfaccia di amministrazione | l'amministratore locale |
| 8084 | servizio SPARQL protetto (`/sparql-protected/`) | HDN Central |
| 8080 | Ontop | solo i due processi precedenti, in locale |

Avviate interfaccia e servizio protetto, ciascuno in un terminale (o con il
vostro gestore di servizi, es. systemd), dalla directory del repository e con
il virtualenv attivo:

```bash
# interfaccia di amministrazione
gunicorn myproject.wsgi:application --bind 127.0.0.1:8000 --workers 2 --threads 2 --timeout 300

# servizio SPARQL protetto per HDN Central
python run_sparql_server.py
```

Ontop **non va avviato a mano**: si avvia e si ferma da *Ontop Monitor*
nell'interfaccia (vedi sotto). L'interfaccia lo arresta e lo riavvia da sola
quando caricate dati o salvate il mapping.

Aprite l'interfaccia su `http://localhost:8000/`.

---

## Prima configurazione

### 1. Caricate i dati

*Upload CSV*: selezionate uno o piu' file CSV.

- **Il nome del file diventa il nome della tabella**, spazi e maiuscole
  compresi. Il template di mapping si aspetta queste tabelle:

  | file | contenuto |
  |---|---|
  | `PATIENTS GENERAL DATA.csv` | una riga per paziente (dati anagrafici, onset, diagnosi, genetica, comorbidita') |
  | `ALS FUNCTIONAL RATING SCALE.csv` | una riga per visita ALSFRS-R |
  | `SPIRO VISITS.csv` | una riga per spirometria |

  Non e' obbligatorio usare questi nomi: al passo successivo potete associare
  ogni blocco del template a qualunque tabella.
- Ricaricare un file con lo stesso nome **sostituisce** la tabella.
- Se un file non si carica, nessuna tabella dell'upload viene modificata.
- I CSV non restano su disco: vengono letti e cancellati.

La pagina mostra lo schema delle tabelle caricate.

### 2. Collegate i dati all'ontologia

*Map Data to HERO*: per ogni blocco del template

1. scegliete la tabella;
2. cliccate un segnaposto a sinistra e poi la colonna corrispondente a destra.

Premete *Generate OBDA File*. Prima di salvare, ogni blocco viene eseguito sui
vostri dati: se uno non funziona il mapping non viene salvato e la pagina dice
quale blocco e perche'. Se una colonna numerica e' stata caricata come testo
(ad esempio perche' contiene codici come `u` per i valori mancanti), il
controllo viene adattato da solo e la pagina ve lo segnala.

Ogni salvataggio conserva la versione precedente in `myapp/obda/mapping-backups/`.

### 3. Scegliete il livello di disclosure

Il selettore *Select Privacy Level*, nella barra laterale, fissa il livello
massimo di dettaglio che l'endpoint restituisce, da L0 (solo risposte si'/no)
a L6 (dati completi). Le query di livello superiore ricevono una risposta
vuota, indistinguibile dall'assenza di dati.

**Il repository arriva impostato su L4: sceglietelo voi prima di collegare
l'endpoint a HDN Central.** Il valore viene applicato appena lo cambiate.

### 4. Avviate Ontop

*Ontop Monitor*: attivate l'interruttore *Running*. L'avvio richiede qualche
secondo; la console mostra il log di Ontop.

### 5. Verificate in locale

*Run SPARQL Query* interroga Ontop direttamente, senza catalogo ne' livelli: e'
lo strumento per controllare il mapping. Esempio:

```sparql
PREFIX bto:  <https://w3id.org/brainteaser/ontology/schema/>
PREFIX NCIT: <http://purl.obolibrary.org/obo/NCIT_>
SELECT (COUNT(DISTINCT ?pat) AS ?n) WHERE {
  ?pat a bto:Patient ;
       bto:hasDisease NCIT:C34373 .
}
```

### 6. Iscrivete l'endpoint a HDN Central

Verificate prima che la porta 8084 sia raggiungibile da Central:

```bash
curl -i http://endpoint.istituzione.example:8084/sparql-protected/
# atteso: HTTP/1.0 405 Method Not Allowed
```

Poi, dalla pagina **HDN Network** dell'interfaccia:

1. inserite l'URL di Central, l'URL con cui Central raggiunge questo endpoint
   (la porta 8084, ad esempio `http://endpoint.istituzione.example:8084/`) e il
   nome dell'istituzione;
2. inviate la candidatura. L'endpoint resta *Waiting for approval* finche'
   l'amministratore di Central non la approva;
3. all'approvazione compare una notifica (campanella nella barra in alto) e lo
   stato diventa *Member*.

Da quel momento Central puo' inviare query, cataloghi delle query e ontologie.

---

## Rete HDN: protocolli con Central

Accanto a `/sparql-protected/`, la porta 8084 espone tre protocolli sotto
`/hdn/`. Ogni messaggio e' firmato con Ed25519 e la risposta e' firmata a sua
volta.

| protocollo | percorso | cosa fa l'endpoint |
|---|---|---|
| iscrizione | `/hdn/enrollment/` | riceve l'esito della candidatura |
| catalogo delle query | `/hdn/catalog/` | installa subito il catalogo, se la versione e' piu' recente |
| ontologia | `/hdn/ontology/` | installa subito l'ontologia, adegua il mapping, riavvia Ontop se era acceso |

- **Chiavi.** L'endpoint genera la propria chiave al primo avvio, in
  `uploads/hdn/identity.pem`. La chiave di Central e' quella ricevuta al primo
  contatto: se lo stesso URL si ripresenta con una chiave diversa, la
  candidatura viene rifiutata. Il primo contatto non e' verificato in altro
  modo, quindi va fatto su una rete di cui vi fidate.
- **Firma.** Ogni richiesta e' legata a azione, destinatario, istante e nonce:
  non si puo' alterare, ripetere o reindirizzare a un altro endpoint. Solo un
  Central approvato puo' inviare cataloghi e ontologie.
- **Catalogo.** Un catalogo ricevuto puo' cambiare testi, livelli e descrizioni
  dei template. Non puo' introdurre nuove grammatiche dei parametri: un
  parametro resta sempre un valore chiuso. Il catalogo nel codice
  (`myapp/catalog.py`) e' la versione 0.
- **Ontologia.** Central la invia sempre insieme al template di mapping
  scritto per essa, cioe' i blocchi che *Map Data to HERO* propone. Il template
  nel repository (`myapp/mappings/template.obda`) vale solo finche' non ne
  arriva uno da Central. L'aggiornamento viene rifiutato per intero, senza
  cambiare nulla, in due casi:
  - l'ontologia ha `owl:imports` non risolvibili offline (`myapp/obda/imports/`);
  - il template usa termini che l'ontologia non dichiara.

  Dal mapping del sito viene eliminato ogni blocco che usa un termine
  dichiarato dall'ontologia precedente e non piu' dalla nuova. La versione
  precedente del mapping resta in `mapping-backups/`. Se non resta alcun
  blocco, Ontop non viene riavviato e il mapping va rifatto.
- **Notifiche.** Iscrizione, cataloghi e ontologie ricevuti o rifiutati, e
  l'esito del riavvio di Ontop, compaiono nella pagina **Notifications**.

Limite noto: `/sparql-protected/` non e' autenticato.

---

## Cosa c'e' dove

| percorso | contenuto | versionato |
|---|---|---|
| `myapp/obda/mydatabase.duckdb` | i dati caricati | no |
| `myapp/obda/hereditary_ontology_2.obda` | il mapping attivo del sito | no |
| `myapp/obda/mapping-backups/` | le ultime 10 versioni del mapping | no |
| `uploads/level.duckdb` | il livello di disclosure scelto | si' (vedi passo 3) |
| `uploads/hdn/identity.pem` | chiave privata dell'endpoint nella rete HDN | no |
| `uploads/hdn/db.sqlite3` | iscrizioni ai Central e notifiche | no |
| `uploads/hdn/catalog.json` | catalogo delle query ricevuto da Central | no |
| `uploads/hdn/ontology/` | ontologia e template di mapping ricevuti da Central, versioni precedenti | no |
| `audit.log` | decisioni dell'endpoint sulle richieste di Central | no |
| `myapp/obda/ontop.log` | log di Ontop, con rotazione a 10 MB (5 file) | no |
| `myapp/obda/ontop.console.log` | output della JVM all'ultimo avvio di Ontop | no |
| `myapp/mappings/template.obda` | template di mapping comune a tutti i siti | si' |
| `myapp/catalog.py` | catalogo delle query ammesse | si' |
| `myapp/obda/hero_clinical.ttl` | ontologia HERO | si' |

Per un backup copiate `myapp/obda/mydatabase.duckdb`,
`myapp/obda/hereditary_ontology_2.obda` e `uploads/level.duckdb`.

**`audit.log`** e' l'unico posto in cui vedere perche' una richiesta di Central
non ha avuto risposta (livello troppo alto, query fuori catalogo, Ontop
spento): verso Central questi casi sono volutamente indistinguibili.

---

## Aggiornamento

```bash
git pull
. .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
```

poi riavviate i due processi. Dati, mapping e livello non vengono toccati.

Il catalogo delle query (`myapp/catalog.py`) deve coincidere con quello di HDN
Central: se Central lo aggiorna, aggiornate anche l'endpoint, altrimenti le
nuove query ricevono risposte vuote.

---

## Problemi frequenti

| sintomo | causa probabile |
|---|---|
| Ontop non parte; la console mostra un errore Java | Java mancante o precedente alla 11: `java -version` |
| Ontop non parte; `ontop.log` cita il mapping | mapping non valido: rigeneratelo da *Map Data to HERO* |
| Upload: "Ontop did not stop in time" | Ontop e' stato avviato fuori dall'interfaccia: fermatelo e riavviatelo da *Ontop Monitor* |
| Central non riceve risultati | guardate `audit.log`: livello troppo basso, query fuori catalogo o Ontop spento |
| Pagine con avviso "database is held by another process" | un altro processo tiene `mydatabase.duckdb` in scrittura |
| La porta 8084 non risponde | `run_sparql_server.py` non e' avviato |

---

## Docker

E' disponibile un `Dockerfile` che avvia interfaccia e servizio protetto con
supervisor, con un utente senza privilegi. Il build dell'immagine non e' ancora
stato verificato; l'installazione descritta sopra si'. Anche nel container
Ontop si avvia da *Ontop Monitor*: il programma `ontop` di `supervisord.conf`
non va usato.

Rendete persistenti `myapp/obda/` e `uploads/` (volumi), altrimenti dati e
mapping si perdono ricreando il container.

---

## Sviluppo

```bash
python manage.py runserver 127.0.0.1:8000   # interfaccia, con ricaricamento
python run_sparql_server.py                  # servizio protetto (porta 8084)
```

`/sparql-protected/` e' servito anche dalla porta 8000.

Test che richiedono strumenti esterni, saltati se non attivati:

```bash
HDN_ONTOP_LIVE=1 python manage.py test myapp.tests          # avvia Ontop reale
HDN_MERMAID_NODE_MODULES=/percorso/node_modules python manage.py test myapp.tests
```

Variabili d'ambiente:

| variabile | effetto |
|---|---|
| `HDN_HTTPS=1` | cookie Secure, redirect HTTPS e HSTS, se l'endpoint e' servito in HTTPS |
| `HDN_SPARQL_PORT` | porta del servizio protetto (default 8084) |
| `HDN_AUDIT_LOG` | percorso dell'audit log |
| `HDN_STATE_DIR` | stato di rete e database (default `uploads/hdn`) |
| `HDN_ONTOP_OWL_LOG_LEVEL=WARN` | mostra nel log di Ontop gli avvisi OWL 2 QL, nascosti di default |

---

## Licenze

Le librerie e le ontologie incluse hanno licenze proprie, indicate in
`myapp/static/myapp/vendor/README`, `myapp/obda/imports/README` e
`myapp/obda/copyright/`.
