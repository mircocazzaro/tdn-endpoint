"""Audit-test del punto 34: una sola definizione dei percorsi, coerente con Ontop.

views.py dichiarava due volte i percorsi. Il primo DUCKDB_PATH era
uploads/mydatabase.duckdb, il secondo myapp/obda/mydatabase.duckdb: valeva il
secondo solo perche' veniva dopo. Riordinare il modulo o togliere il secondo
blocco faceva lavorare la UI su un database diverso e vuoto, mentre Ontop
continuava a servire l'altro.
"""

import ast
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from myapp import ontop_process, views

VIEWS = Path(settings.BASE_DIR) / "myapp" / "views.py"


def _module_assignments(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
    return names


class PathsTests(SimpleTestCase):

    def test_every_module_constant_is_assigned_once(self):
        names = _module_assignments(VIEWS)
        duplicated = sorted({n for n in names if n.isupper() and names.count(n) > 1})
        self.assertEqual(duplicated, [])

    def test_admin_database_is_the_one_ontop_opens(self):
        props = Path(ontop_process.PROPS_FILE).read_text(encoding="utf-8")
        url = re.search(r"^jdbc\.url\s*=\s*jdbc:duckdb:(.+)$", props, re.M).group(1).strip()
        ontop_db = (Path(ontop_process.ONTOP_DIR) / url).resolve()
        self.assertEqual(Path(views.DUCKDB_PATH).resolve(), ontop_db)

    def test_mapping_and_log_paths_are_ontops(self):
        self.assertEqual(Path(views.OBDA_FILE), Path(ontop_process.OBDA_FILE))
        self.assertEqual(Path(views.LOG_FILE), Path(ontop_process.LOG_FILE))
