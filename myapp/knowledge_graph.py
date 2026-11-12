"""Knowledge graph RDF materializzato da Ontop, con cache nello stato dell'app.

Il file e' valido finche' non cambiano cio' che lo determina: mapping, ontologia,
proprieta' di connessione, sorgente attiva e dati del sito. Ogni cambio di dati
(upload CSV, eliminazione tabella, cambio sorgente) invalida la cache; un
mapping salvato invalida solo se il suo contenuto cambia.

Materializzazione: comando `ontop materialize` (la CLI di Ontop; il server
endpoint non espone materialize via HTTP).
"""
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from django.conf import settings

from . import ontop_process

FORMAT = "turtle"
EXTENSION = "ttl"
TIMEOUT = 900  # secondi: un grafo grande puo' richiedere minuti
_lock = threading.Lock()


class KnowledgeGraphError(Exception):
    pass


def _dir():
    return Path(settings.HDN_STATE_DIR) / "knowledge_graph"


def _data_path():
    return _dir() / f"knowledge_graph.{EXTENSION}"


def _meta_path():
    return _dir() / "meta.json"


def has_mappings():
    """True se il mapping attivo contiene almeno una regola."""
    mapping, _ = ontop_process.active_files()
    try:
        return "mappingId" in Path(mapping).read_text(encoding="utf-8")
    except FileNotFoundError:
        return False


def _cache_key():
    from .ontology import active_path
    from . import site
    mapping, props = ontop_process.active_files()
    h = hashlib.sha256()
    for part in (Path(mapping), Path(props), Path(active_path())):
        h.update(part.name.encode() + b"\0")
        h.update(part.read_bytes())
        h.update(b"\0")
    h.update(f"format={FORMAT};source={site.active()}".encode())
    return h.hexdigest()


def status():
    """{'available': bool, 'cached': bool} per la pagina."""
    available = has_mappings()
    if not available:
        return {"available": False, "cached": False}
    try:
        meta = json.loads(_meta_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"available": True, "cached": False}
    cached = meta.get("key") == _cache_key() and _data_path().exists()
    return {"available": True, "cached": cached,
            "created": meta.get("created"), "size": meta.get("size"),
            "ontology_applied": meta.get("ontology_applied", True),
            "ontology_note": meta.get("ontology_note", "")}


def invalidate():
    """Elimina il file in cache: da chiamare quando cambiano i dati del sito."""
    for p in (_data_path(), _meta_path()):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def _write_atomic(target, write):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".")
    os.close(fd)
    try:
        write(Path(tmp))
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _ontop_error(text):
    """Causa dell'errore di Ontop: la prima riga ERROR, o l'ultima eccezione."""
    import re
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    errors = [l for l in lines if " ERROR " in l or l.startswith("ERROR")]
    picks = errors or [l for l in lines if "Exception" in l or "Caused by" in l]
    line = picks[0] if picks else (lines[-1] if lines else "no output")
    line = re.sub(r"^\d{2}:\d{2}:\d{2}\.\d+ \|-\w+\s+", "", line)
    return line[:300]


def _run_ontop(out_file, with_ontology):
    from .ontology import active_path
    mapping, props = ontop_process.active_files()
    cmd = [str(ontop_process.ONTOP_CMD), "materialize",
           "-m", str(mapping), "-p", str(props),
           "-x", str(ontop_process.XML_CATALOG), "-f", FORMAT, "-o", str(out_file)]
    if with_ontology:
        cmd += ["-t", str(active_path())]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT,
                              cwd=str(ontop_process.ONTOP_DIR))
    except subprocess.TimeoutExpired:
        raise KnowledgeGraphError(f"Ontop did not finish within {TIMEOUT // 60} minutes")
    if proc.returncode != 0:
        raise KnowledgeGraphError(_ontop_error(proc.stdout + "\n" + proc.stderr))
    if not out_file.exists() or out_file.stat().st_size == 0:
        raise KnowledgeGraphError("Ontop produced an empty graph")


def materialize():
    """Materializza con ontop materialize e aggiorna la cache.

    Prima con l'ontologia (inferenze incluse). Se Ontop non ci riesce, rigenera
    senza ontologia: solo i triple prodotti dal mapping. Il motivo resta nei
    metadati e viene mostrato quando il file viene scaricato.
    """
    key = _cache_key()
    _dir().mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="kg-", dir=_dir()))
    out = workdir / f"graph.{EXTENSION}"
    note = ""
    try:
        with _lock:
            try:
                _run_ontop(out, with_ontology=True)
            except KnowledgeGraphError as exc:
                note = str(exc)
                out.unlink(missing_ok=True)
                _run_ontop(out, with_ontology=False)
            _write_atomic(_data_path(), lambda target: os.replace(out, target))
            meta = {"key": key, "format": FORMAT, "created": time.time(),
                    "size": _data_path().stat().st_size,
                    "ontology_applied": not note, "ontology_note": note}
            _write_atomic(_meta_path(), lambda p: p.write_text(json.dumps(meta), encoding="utf-8"))
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)
    return _data_path(), meta


def get_or_materialize():
    """Ritorna (percorso, dalla_cache, metadati). Rigenera solo se la cache non e' valida."""
    if not has_mappings():
        raise KnowledgeGraphError("there is no mapping: create one in Map Data to HERO")
    if status().get("cached"):
        meta = json.loads(_meta_path().read_text(encoding="utf-8"))
        return _data_path(), True, meta
    path, meta = materialize()
    return path, False, meta
