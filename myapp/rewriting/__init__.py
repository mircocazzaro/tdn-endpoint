"""Verificatore di riscrittura sul catalogo, portato da HDN Central.

``ir``, ``minicon`` e ``admission`` sono copie di
``central-tdn/catalogapp/rewriting`` (``ir`` cambia solo per ricevere il
catalogo come dato). ``service`` e' nuovo: legge il catalogo attivo
dell'endpoint invece del database Django di Central.
"""
