"""Catalogo delle query ammesse dall'HDN Endpoint.

E' la copia locale del catalogo di HDN Central (D3.2 sez. 2.3.2 e 2.4.1): per
ogni template il livello di disclosure, una descrizione, i parametri con la loro
grammatica e il testo SPARQL con i segnaposto ``{nome}``.

Invarianti verificate all'import del modulo:

- l'identificatore di ogni template e' lo SHA-512 del suo testo, come in
  ``central-tdn/catalogapp/queries.py``, ed e' fissato accanto al testo. Se il
  testo cambia anche di un solo byte, ad esempio perche' un editor rimuove gli
  spazi finali o cambia i fine riga, il modulo rifiuta di caricarsi invece di
  far divergere in silenzio l'endpoint dal catalogo federato;
- ogni segnaposto del testo ha una grammatica dichiarata, e ogni parametro
  dichiarato compare nel testo;
- chiavi e hash sono unici, i livelli sono compresi fra L0 e L6.

Il testo di ogni template e' scritto riga per riga con ``_lines``: spazi finali,
tabulazioni e caratteri non ASCII restano visibili come escape e non dipendono
dall'editor. Deve restare identico byte per byte a quello di Central, perche' e'
Central a istanziarlo.

Le chiavi ``qNN_LX`` riprendono la posizione del template nel catalogo di
Central (l'``id`` dei suoi form) e il livello, come nelle figure di D3.2.
"""

import hashlib
import re
from dataclasses import dataclass
from types import MappingProxyType

LEVELS = range(0, 7)

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class CatalogIntegrityError(RuntimeError):
    """Il catalogo non rispetta le proprie invarianti."""


@dataclass(frozen=True)
class ParamType:
    """Grammatica chiusa del valore di un parametro.

    ``pattern`` descrive per intero i valori ammessi e si applica con
    ``re.fullmatch``. Nessun valore ammesso contiene spazi, apici, ``#``,
    parentesi o graffe: un parametro occupa sempre e solo la posizione in cui il
    template lo colloca, e non puo' aggiungere SPARQL alla query.
    """

    name: str
    pattern: str
    description: str

    def accepts(self, value):
        return isinstance(value, str) and re.fullmatch(self.pattern, value) is not None


# Concetto NCIT nella forma prefissata che il form di Central produce
# (DISEASE_MAP in central-tdn/catalogapp/forms.py).
DISEASE = ParamType(
    name="disease",
    pattern=r"NCIT:C[1-9][0-9]{0,9}",
    description="concetto NCIT della malattia, es. NCIT:C34373",
)

# Eta' in anni. Compare sia come literal numerico, FILTER(?aO < {age}), sia
# dentro le etichette fra doppi apici delle fasce di eta', "<{age1}".
AGE = ParamType(
    name="age",
    pattern=r"[0-9]{1,3}(?:\.[0-9]{1,2})?",
    description="eta' in anni, es. 40 o 40.5",
)

# Il valore finisce dentro un literal fra doppi apici, bto:sex "{sex}", quindi
# la grammatica ammette solo lettere.
SEX = ParamType(
    name="sex",
    pattern=r"[A-Za-z]{1,16}",
    description="sesso come registrato nei dati, es. female",
)

# Una delle 12 domande ALSFRS-R, nella forma IRI completa che il form di Central
# propone (QUESTION_CHOICES in central-tdn/catalogapp/forms.py).
ALSFRS_QUESTION = ParamType(
    name="alsfrs_question",
    pattern=r"<https://w3id\.org/brainteaser/ontology/schema/alsfrs(?:[1-9]|1[0-2])>",
    description="IRI di una domanda ALSFRS-R, da alsfrs1 a alsfrs12",
)


@dataclass(frozen=True, eq=False)
class Template:
    key: str
    level: int
    description: str
    params: "MappingProxyType[str, ParamType]"
    sha512: str
    sparql: str

    def __post_init__(self):
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))

    @property
    def placeholders(self):
        """Segnaposto del testo, in ordine di prima comparsa."""
        return list(dict.fromkeys(PLACEHOLDER_RE.findall(self.sparql)))

    @property
    def wire_sha512(self):
        """Hash del testo nella forma in cui Central lo invia nel campo ``template``.

        Prima di inviarlo Central sostituisce ``<{`` con ``**<`` e ``}>`` con
        ``>**`` (residuo dei vecchi segnaposto ``<{nome}>``). Sui template
        attuali la sostituzione cambia solo q07_L3, dove l'etichetta
        ``"<{age1}"`` contiene la sequenza ``<{``.
        """
        return hashlib.sha512(central_mask(self.sparql).encode("utf-8")).hexdigest()

    def __repr__(self):
        return f"<Template {self.key} L{self.level}>"


def _lines(*lines):
    return "\n".join(lines)


def central_mask(text):
    """Trasformazione che Central applica al campo ``template`` prima dell'invio."""
    return text.replace("<{", "**<").replace("}>", ">**")


# Prologo con cui Central istanzia ogni template prima di inviarlo
# (variabile ``prefixes`` in central-tdn/catalogapp/views.py), riprodotto byte
# per byte. Sono i namespace rispetto ai quali i template sono scritti.
PROLOGUE = _lines(
    'PREFIX bto:   <https://w3id.org/brainteaser/ontology/schema/>',
    'PREFIX skos:  <http://www.w3.org/2004/02/skos/core#>',
    'PREFIX xsd:   <http://www.w3.org/2001/XMLSchema#>',
    'PREFIX NCIT:  <http://purl.obolibrary.org/obo/NCIT_>',
    '',
)

PREFIXES = MappingProxyType(dict(
    re.findall(r"PREFIX\s+([A-Za-z][A-Za-z0-9_-]*):\s*<([^>]*)>", PROLOGUE)
))


# Template di Central non inclusi:
#
#   q05_L2  tempo di sopravvivenza mediano dall'onset al decesso. Usa
#           bto:registeredFor, che HERO non dichiara, e il suo FILTER confronta
#           l'IRI della proprieta' bto:eventStart invece di una variabile.
#           L'endpoint non lo riconosceva gia' prima di questo modulo. Va
#           riscritto o tolto dal catalogo federato.

CATALOG = (
    Template(
        key='q00_L0',
        level=0,
        description='Is there any patient diagnosed with DISEASE?',
        params={'disease': DISEASE},
        sha512=(
            '2b38956263bba61044d8b2e898df1880a83ad3c8bce23c43f84f30ad1d30e587'
            '79173ecd8c609193fb9397a858239cb91332c45aebcb2b7f8eccd9a8cb75a663'
        ),
        sparql=_lines(
            'ASK WHERE {',
            '  ?pat a bto:Patient ;',
            '       bto:hasDisease  {disease} .',
            '}',
        ),
    ),
    Template(
        key='q01_L0',
        level=0,
        description='Is there any DISEASE patient under AGE years old?',
        params={'disease': DISEASE, 'age': AGE},
        sha512=(
            'ef1c7e06dc45f63a87887158f2e6af7338e41d4727ca8bf62fec6686ad7e8070'
            '888d989357c0e0974c63e4600ecb34dd11375b377a22f294de04140a91d6a21f'
        ),
        sparql=_lines(
            'ASK WHERE {',
            '  ?pat a bto:Patient ;',
            '       bto:hasDisease {disease} ;',
            '       bto:undergo ?eO.',
            '  ?eO  a bto:Onset ;',
            '       bto:ageOnset ?aO .',
            '  FILTER(?aO < {age})',
            '}',
        ),
    ),
    Template(
        key='q02_L1',
        level=1,
        description='How many patients are diagnosed with DISEASE?',
        params={'disease': DISEASE},
        sha512=(
            '3ade72ee584fd40c1aa5d15bd8c9a6bcdedc8e5f33494aac338a60f40081e376'
            'f1c85522cc2a8cb9e16b61a7914f014262e89d06bf01971ecdc1ba31d3b92c32'
        ),
        sparql=_lines(
            'SELECT (COUNT(DISTINCT ?pat) AS ?nDISEASE) WHERE {',
            '  ?pat a bto:Patient ;',
            '       bto:hasDisease {disease} .',
            '}',
        ),
    ),
    Template(
        key='q03_L1',
        level=1,
        description='How many patients have DISEASE filtered by SEX?',
        params={'disease': DISEASE, 'sex': SEX},
        sha512=(
            'bf4a6509fabddd04dffd84318d58abf1ef0ecf42b41efd0a8c80f66acb1c0073'
            '75e9c55cf37fb45446341d97f0d1f981d8e87051464cb0ea5f5e8c0bcf6c1ee9'
        ),
        sparql=_lines(
            'SELECT (COUNT(DISTINCT ?pat) AS ?nSex) WHERE {',
            '  ?pat a bto:Patient ;',
            '       bto:hasDisease {disease} ;',
            '       bto:sex "{sex}" .',
            '}',
        ),
    ),
    Template(
        key='q04_L2',
        level=2,
        description='What is the average age at onset of DISEASE patients?',
        params={'disease': DISEASE},
        sha512=(
            '8debe5df598a877a57d58f534fd781f31924b02adb7c49db4df6b3b62ea98532'
            'c69e189d94364ba1b66c41bd9a3ec458c31d6189280cb297f5ef162209dbf279'
        ),
        sparql=_lines(
            'SELECT (AVG(?aO) AS ?avgAge) WHERE {',
            '  ?pat a bto:Patient ;',
            '       bto:hasDisease {disease} ;',
            '       bto:undergo ?eO.',
            '  ?eO  a bto:Onset ;',
            '       bto:ageOnset ?aO .',
            '}',
        ),
    ),
    Template(
        key='q06_L3',
        level=3,
        description='Average age at onset grouped by bulbar vs spinal (ALS-specific)',
        params={},
        sha512=(
            '1ff81106904443fb588bf4e6961ea3365657be3ead8215d32d32069772e322d5'
            '497c4709d4e7867086980c7ee49cc9e726c4341570b07863af17ca9afe3994b8'
        ),
        sparql=_lines(
            'SELECT ?site (AVG(?ageOn) AS ?avgOnsetAge) ',
            'WHERE {',
            '  ?pat a bto:Patient ;',
            '       bto:hasDisease NCIT:C34373 ;',
            '       bto:undergo ?ev .',
            '  ?ev  a bto:Onset ;',
            '       bto:ageOnset ?ageOn ;',
            '       bto:bulbarOnset ?b .',
            '  BIND(IF(?b = true,"Bulbar","Spinal") AS ?site)',
            '}',
            'GROUP BY ?site',
        ),
    ),
    Template(
        key='q07_L3',
        level=3,
        description='Count of DISEASE patients by age bracket',
        params={'disease': DISEASE, 'age1': AGE, 'age2': AGE, 'age3': AGE},
        sha512=(
            'c17889385d3976b01b65abfdeb6eac1158f32cfaab62b99698e701ddee88587d'
            'fcdc5308cc1ce343a32d4f07be796428c90882f8dd1ea654b66ce51a330de434'
        ),
        sparql=_lines(
            'SELECT ?bracket ?n WHERE {',
            'SELECT ?bracket (AVG(?ageOn) AS ?avgAgeOn) (COUNT(DISTINCT ?pat) AS ?n) WHERE {',
            '  ?pat a bto:Patient ;',
            '       bto:hasDisease {disease} ;',
            '       bto:undergo ?ev .',
            '  ?ev  a bto:Onset ;',
            '       bto:ageOnset ?ageOn .',
            '  BIND(',
            '    IF(?ageOn < {age1}, "<{age1}",',
            '      IF(?ageOn <= {age2}, "{age1}\u2013{age2}", IF(?ageOn <= {age3}, "{age2}-{age3}", ">{age3}")',
            '    ) ) AS ?bracket',
            '  )',
            '}',
            'GROUP BY ?bracket',
            'ORDER BY ?avgAgeOn',
            '}',
        ),
    ),
    Template(
        key='q08_L3',
        level=3,
        description='Average ALSFRS score for a selected question (with human-readable text and grading)',
        params={'question': ALSFRS_QUESTION},
        sha512=(
            '4526a48277c9bba723092b085ef370a4496fd1a3d14756f38a3ff6a62322abc6'
            'd37327c19d96c91ae8c5502cbfa94759c9d522ea7d78324b65850ae867b8cddd'
        ),
        sparql=_lines(
            'SELECT ?question ?avgq ?grad WHERE {',
            '  {',
            '    SELECT ?question (AVG(?q) AS ?avgq) ',
            '    WHERE {',
            '      ?pat a bto:Patient ;',
            '           bto:hasDisease NCIT:C34373 ;',
            '           bto:undergo ?ev .',
            '      ?ev  bto:consists ?alsfrs .',
            '      ?alsfrs a bto:ALSFRS ;',
            '              ?quest ?q .',
            '',
            '      # <-- user-provided question URI goes here',
            '      FILTER (?quest = {question})',
            '',
            '      BIND(IF (',
            '          ?quest = <https://w3id.org/brainteaser/ontology/schema/alsfrs1>, "I have been less alert", IF (',
            '          ?quest = <https://w3id.org/brainteaser/ontology/schema/alsfrs2>, "I have had difficulty paying attention for long periods of time", IF (',
            '          ?quest = <https://w3id.org/brainteaser/ontology/schema/alsfrs3>, "I have been unable to think clearly", IF (',
            '          ?quest = <https://w3id.org/brainteaser/ontology/schema/alsfrs4>, "I have been clumsy and uncoordinated", IF (',
            '          ?quest = <https://w3id.org/brainteaser/ontology/schema/alsfrs5>, "I have been forgetful", IF (',
            '          ?quest = <https://w3id.org/brainteaser/ontology/schema/alsfrs6>, "I have had to pace myself in my physical activities", IF (',
            '          ?quest = <https://w3id.org/brainteaser/ontology/schema/alsfrs7>, "I have been less motivated to do anything that requires physical effort", IF (',
            '          ?quest = <https://w3id.org/brainteaser/ontology/schema/alsfrs8>, "I have been less motivated to participate in social activities", IF (',
            '          ?quest = <https://w3id.org/brainteaser/ontology/schema/alsfrs9>, "I have been limited in my ability to do things away from home", IF (',
            '          ?quest = <https://w3id.org/brainteaser/ontology/schema/alsfrs10>, "I have trouble maintaining physical effort for long periods", IF (',
            '          ?quest = <https://w3id.org/brainteaser/ontology/schema/alsfrs11>, "I have had difficulty making decisions", IF (',
            '          ?quest = <https://w3id.org/brainteaser/ontology/schema/alsfrs12>, "I have been less motivated to do anything that requires thinking", "" ) ) ) ) ) ) ) ) ) ) )) ',
            '        ',
            '        AS ?question )',
            '    }',
            '    GROUP BY ?question',
            '  }',
            '  BIND(',
            '    IF(?avgq < 0.5, "Never",',
            '    IF(?avgq < 1.5, "Rarely",',
            '    IF(?avgq < 2.5, "Sometimes",',
            '    IF(?avgq < 3.5, "Often",',
            '       "Almost Always"))))',
            '    AS ?grad',
            '  )',
            '}',
        ),
    ),
    Template(
        key='q09_L4',
        level=4,
        description='List ages & sexes of DISEASE patients (anonymized)',
        params={'disease': DISEASE},
        sha512=(
            '5b8a44bf0364eb71dd9234abe7cd76f215afd560b8fab2ced3c8a15739dfcc79'
            '13aae62a8eca876325ef9000b6be026800c24b3959a8b5c93eb779945ffb1d9a'
        ),
        sparql=_lines(
            'SELECT ?ageOn ?sex WHERE {',
            '  ?pat a bto:Patient ;',
            '       bto:hasDisease {disease} ;',
            '       bto:sex ?sex ;',
            '       bto:undergo ?ev .',
            '  ?ev  a bto:Onset ;',
            '       bto:ageOnset ?ageOn .',
            '}',
        ),
    ),
    Template(
        key='q10_L4',
        level=4,
        description='Count of DISEASE patients by onset-type combinations (Axial, Bulbar, General, Limbs)',
        params={'disease': DISEASE},
        sha512=(
            '58d92e8b94bbbb84658e4b1096941a1dad7b6f8b98bb3f3239160161c4b4890c'
            '16b50a90217c97925839c49b96351bd555a0ea8d08880e2c3fc023b6db0e8807'
        ),
        sparql=_lines(
            'SELECT ?onsetTypes (COUNT(DISTINCT ?pat) AS ?n) WHERE {',
            '  ?pat a bto:Patient ;',
            '       bto:hasDisease {disease} ;',
            '       bto:undergo ?ev .',
            '  ?ev  a bto:Onset ;',
            '       bto:eventStart     ?tDate ;',
            '       bto:bulbarOnset  ?bOns ;',
            '       bto:axialOnset  ?aOns ;',
            '       bto:generalizedOnset  ?gOns ;',
            '       bto:limbsOnset  ?lOns .',
            '  BIND(',
            '  \tCONCAT(',
            '    \tIF(?aOns = true, "Axial", ""),',
            '\t\tIF(?bOns = true, "Bulbar", ""),',
            '        IF(?gOns = true, "General", ""),',
            '        IF(?lOns = true, "Limbs", "")',
            '    ) AS ?onsetTypes',
            '   )',
            '}',
            'GROUP BY ?onsetTypes',
        ),
    ),
    Template(
        key='q11_L5',
        level=5,
        description='Anonymized ALS-onset profile (MD5 pat, age, onset age, bulbar)',
        params={'disease': DISEASE},
        sha512=(
            'bcdb63546def4177456fe7400aa535edf62ac8463f9639c0bc7a4a7a322528af'
            'd7d141964d6857743d394caa2061f33f605617c59ffc07b1fef2fd99cea4f55c'
        ),
        sparql=_lines(
            'SELECT (MD5(STR(?pat)) AS ?anonID) ?ageOn ?b WHERE {',
            '  ?pat a bto:Patient ;',
            '       bto:hasDisease  {disease} ;',
            '        bto:undergo ?ev .',
            '  ?ev  a bto:Onset ;',
            '       bto:ageOnset    ?ageOn ;',
            '       bto:bulbarOnset ?b .',
            '}',
        ),
    ),
    Template(
        key='q12_L5',
        level=5,
        description='Anonymized onset profile: MD5 hash of patient URI, age at onset, and bulbar-onset flag for DISEASE patients',
        params={'disease': DISEASE},
        sha512=(
            'c208b60883a18b761c21fabe30d1c62e88c5f0a5b8c50902e6f1505322f7da88'
            '74981003c1fceaa4c3ee01989aa1f3fc2854597445245a53ca1aa56f6ad65cd5'
        ),
        sparql=_lines(
            'SELECT (MD5(STR(?pat)) AS ?anonID)',
            '       ?tDate ?onsetTypes WHERE {',
            '  ?pat a bto:Patient ;',
            '       bto:hasDisease    {disease} ;',
            '       bto:undergo       ?ev .',
            '  ?ev  a bto:Onset ;',
            '       bto:eventStart     ?tDate ;',
            '       bto:bulbarOnset  ?bOns ;',
            '       bto:axialOnset  ?aOns ;',
            '       bto:generalizedOnset  ?gOns ;',
            '       bto:limbsOnset  ?lOns .',
            '  BIND(',
            '  \tCONCAT(',
            '    \tIF(?aOns = true, "Axial", ""),',
            '\t\tIF(?bOns = true, "Bulbar", ""),',
            '        IF(?gOns = true, "General", ""),',
            '        IF(?lOns = true, "Limbs", "")',
            '    ) AS ?onsetTypes',
            '   )',
            '}',
            'ORDER BY ?anonID ?tDate',
        ),
    ),
    Template(
        key='q13_L6',
        level=6,
        description='All data for DISEASE patients (including IDs)',
        params={'disease': DISEASE},
        sha512=(
            '1f996ff13f55a035aca3cd09a68ee2e8c28df42103d58d8fb08baa4cc033ba3f'
            '016269eb03511ebfba6cf004cd67b88d756731d49e5aeb72c483cb2362f86e83'
        ),
        sparql=_lines(
            'SELECT * WHERE {',
            '  ?pat a bto:Patient ;',
            '       bto:hasDisease  {disease} ;',
            '       ?p ?o .',
            '}',
        ),
    ),
    Template(
        key='q14_L6',
        level=6,
        description='Complete patient profiles for DISEASE',
        params={'disease': DISEASE},
        sha512=(
            'eea9ba3ad603cfced1ca7e1d230f102bf3387c44f4b9379614e7576758f26c26'
            '0cff43ace25d4bf44307ab141420b39383db1609214060d15670009ad7375421'
        ),
        sparql=_lines(
            'SELECT ?pat ?name ?aOns ?sex ?ev ?evType ?evStart  WHERE {',
            '  ?pat a bto:Patient ;',
            '       bto:sex           ?sex ;',
            '       bto:undergo      ?ev ;',
            '       bto:hasDisease    {disease} .',
            '  ?ev  a ?evType ;',
            '       bto:ageOnset ?aOns ;',
            '       bto:eventStart    ?evStart .',
            '}',
        ),
    ),
)


def verify(catalog):
    """Solleva CatalogIntegrityError se ``catalog`` viola le invarianti."""
    problems = []
    keys, hashes = set(), set()

    for t in catalog:
        actual = hashlib.sha512(t.sparql.encode("utf-8")).hexdigest()
        if actual != t.sha512:
            problems.append(
                f"{t.key}: il testo non corrisponde all'hash fissato "
                f"(atteso {t.sha512[:16]}..., attuale {actual[:16]}...). "
                f"Spazi finali o fine riga modificati?"
            )
        if t.level not in LEVELS:
            problems.append(f"{t.key}: livello {t.level} fuori da L0-L6")
        if t.key in keys:
            problems.append(f"{t.key}: chiave duplicata")
        if t.sha512 in hashes:
            problems.append(f"{t.key}: hash duplicato")
        keys.add(t.key)
        hashes.add(t.sha512)

        found = set(t.placeholders)
        declared = set(t.params)
        for name in sorted(found - declared):
            problems.append(f"{t.key}: segnaposto {{{name}}} senza grammatica")
        for name in sorted(declared - found):
            problems.append(f"{t.key}: parametro {name} dichiarato ma assente dal testo")

        for name, ptype in t.params.items():
            try:
                compiled = re.compile(ptype.pattern)
            except re.error as exc:
                problems.append(f"{t.key}: grammatica di {name} non valida: {exc}")
                continue
            if compiled.groups:
                problems.append(
                    f"{t.key}: la grammatica di {name} contiene gruppi di cattura")

    if problems:
        raise CatalogIntegrityError("\n".join(problems))


verify(CATALOG)

BY_KEY = MappingProxyType({t.key: t for t in CATALOG})


def lookup_by_template_text(text):
    """Template il cui testo, in chiaro o mascherato da Central, ha hash ``text``.

    E' il riconoscimento che l'endpoint ha sempre fatto sul campo ``template``
    della richiesta. Restituisce None se il testo non e' nel catalogo.
    """
    h = hashlib.sha512(text.encode("utf-8")).hexdigest()
    return _BY_ANY_HASH.get(h)


_BY_ANY_HASH = MappingProxyType(
    {**{t.wire_sha512: t for t in CATALOG}, **{t.sha512: t for t in CATALOG}}
)
