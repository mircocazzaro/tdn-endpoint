"""Ultime righe di un file di log, con costo limitato.

Ontop Monitor le chiede ogni pochi secondi. La versione precedente leggeva il
file all'indietro a blocchi di 1 KB e a ogni blocco rifaceva splitlines() su
tutto il buffer accumulato: costo quadratico nei byte letti, e con righe
lunghe o un file senza a capo leggeva l'intero file a ogni richiesta.

Qui la lettura procede a blocchi di 64 KB contando solo i '\\n' del blocco
nuovo, e si ferma comunque dopo ``max_bytes``. Ogni riga restituita e' troncata
a ``max_line`` caratteri.
"""

import os

CHUNK = 64 * 1024


def tail_lines(path, n=200, max_bytes=512 * 1024, max_line=2000):
    try:
        f = open(path, "rb")
    except FileNotFoundError:
        return []
    with f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        pos, chunks, newlines = end, [], 0
        while pos > 0 and newlines <= n and end - pos < max_bytes:
            step = min(CHUNK, pos, max_bytes - (end - pos))
            pos -= step
            f.seek(pos)
            chunk = f.read(step)
            chunks.append(chunk)
            newlines += chunk.count(b"\n")
    data = b"".join(reversed(chunks))
    lines = data.splitlines()
    if pos > 0 and lines:
        # La prima riga e' iniziata prima del punto da cui si e' letto.
        lines = lines[1:] if newlines else [b"[...] " + lines[0][-max_line:]]
    out = []
    for raw in lines[-n:]:
        text = raw.decode("utf-8", "replace")
        out.append(text if len(text) <= max_line else text[:max_line] + " [...]")
    return out
