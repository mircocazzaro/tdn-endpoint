# Trusted Data Network - Potential Queries

PREFIX bto:   <https://w3id.org/brainteaser/ontology/schema/>
PREFIX :      <https://w3id.org/hereditary/ontology/phenoclinical/schema/>
PREFIX skos:  <http://www.w3.org/2004/02/skos/core#>
PREFIX xsd:   <http://www.w3.org/2001/XMLSchema#>


## Level 0

### 1. **Is there any patient diagnosed with DISEASE?**

```sparql
ASK WHERE {
  ?pat a bto:Patient ;
       bto:hasDisease  **<disease>** .
}
```

Returns true if at least one `bto:Patient` has the disease concept “Amyotrophic Lateral Sclerosis” (NCIT_C0002736).

PARAMETER(S): **`disease`**

### **2. Is there any DISEASE patient under AGE years old?**

```sparql
ASK WHERE {
  ?pat a bto:Patient ;
       bto:hasDisease **<disease>** ;
       bto:birthYear ?by .
  FILTER((2025 - xsd:integer(?by)) < **<age>**)
}
```

Checks for existence of at least one ALS patient whose computed age in 2025 is under 40.

PARAMETER(S): **`disease` , `age`** 

---

## Level 1

### 1. How many patients are diagnosed with DISEASE?

```sparql
SELECT (COUNT(DISTINCT ?pat) AS ?nDISEASE)
WHERE {
  ?pat a bto:Patient ;
       bto:hasDisease **<disease>** .
}
```

Counts distinct patients with the ALS diagnosis.

PARAMETER(S): **`disease`**

### **2. How many patients have DISEASE filtered by SEX?**

```sparql
SELECT (COUNT(DISTINCT ?pat) AS ?nSex)
WHERE {
  ?pat a bto:Patient ;
       bto:hasDisease **<disease>** ;
       bto:sex **<sex>** .
}
```

Counts distinct ALS patients whose `bto:sex` is “female.”

PARAMETER(S): **`disease` , `sex`** 

---

## Level 2

### 1. What is the average age of DISEASE patients?

```sparql
SELECT (AVG(2025 - xsd:integer(?by)) AS ?avgAge)
WHERE {
  ?pat a bto:Patient ;
       bto:hasDisease **<disease>** ;
       bto:birthYear ?by .
}
```

Computes the mean of (2025 – birthYear) across all ALS patients.

PARAMETER(S): **`disease`**  

### **2. What is the median survival time (in days) from onset to death for** DISEASE **patients, starting from a specific date?**

```sparql
SELECT (AVG(xsd:integer(?diff)) AS ?medianSurvivalDays)
WHERE {
  ?pat a bto:Patient ;
       bto:hasDisease **<disease>** ;
       bto:deathDate ?d .
  ?ev  a bto:Onset ;
       bto:eventStart ?s ;
       bto:registeredFor ?pat .
  FILTER ( bto:eventStart >= "<**starting_date**>"^^xsd:date ) 
  BIND( xsd:integer(?d) - xsd:integer(?s)
        AS ?diff )
}
```

Computes the mean of survival days from `eventStart` (onset) to `deathDate` , starting from a specific date.

PARAMETER(S): **`disease` , `starting_date`**

---

## Level 3

### 1. Average age at onset grouped by bulbar vs spinal onset (ALS-specific):

```sparql
SELECT ?site (AVG(?ageOn) AS ?avgOnsetAge)
WHERE {
  ?pat a bto:Patient ;
       bto:hasDisease <http://purl.obolibrary.org/obo/NCIT_C0002736> ;
       bto:hasEvent ?ev .
  ?ev  a bto:Onset ;
       bto:ageOnset ?ageOn ;
       bto:bulbarOnset ?b .
  BIND(IF(?b = true,"Bulbar","Spinal") AS ?site)
}
GROUP BY ?site
```

Groups ALS patients by bulbar‐onset (true/false) and computes mean age at onset for each group.
PARAMETER(S): none

### **2. Count of** DISEASE **patients by age bracket (<DATE1, DATE1-DATE2, >DATE2)**

```sparql
SELECT ?bracket (COUNT(DISTINCT ?pat) AS ?n)
WHERE {
  ?pat a bto:Patient ;
       bto:hasDisease **<disease>** ;
       bto:birthYear ?by .
  BIND(2025 - xsd:integer(?by) AS ?age)
  BIND(
    IF(?age < **<date1>**, "<**<date1>**",
      IF(?age <= **<date2>**, "**<date1>**–**<date2>**", ">**<date3>**")
    ) AS ?bracket
  )
}
GROUP BY ?bracket
```

Breaks ALS patients into three age groups and counts each.

PARAMETER(S): **`disease` , `date1` ,`date2` ,`date3`**

---

## Level 4

### 1. List ages and sexes of DISEASE patients (no IDs):

```sparql
SELECT (2025 - xsd:integer(?by) AS ?age) ?sex
WHERE {
  ?pat a bto:Patient ;
       bto:hasDisease **<disease>** ;
       bto:birthYear ?by ;
       bto:sex      ?sex .
}
```

Reveals only computed `?age` and `?sex` for each ALS patient—identities hidden.

PARAMETER(S): **`disease`**

### **2. Summary of first‐line therapeutic procedures for** DISEASE **patients:**

```sparql
SELECT ?treatType (COUNT(DISTINCT ?pat) AS ?n)
WHERE {
  ?pat a bto:Patient ;
       bto:hasDisease **<disease>** ;
       bto:hasEvent ?ev .
  ?ev  a bto:TherapeuticProcedure ;
       bto:procedureType ?treatType .
}
GROUP BY ?treatType
```

Aggregates the types of therapeutic procedures (e.g., NIV, PEG) among ALS patients without individual links.

PARAMETER(S): **`disease`** 

---

## Level 5

### 1. Anonymized ALS‐onset profile (age, onset age, bulbar) (ALS-specific):

```sparql
SELECT (SHA256(STR(?pat)) AS ?anonID)
       (2025 - xsd:integer(?by) AS ?age)
       ?ageOn
       ?b
WHERE {
  ?pat a bto:Patient ;
       bto:hasDisease  <http://purl.obolibrary.org/obo/NCIT_C0002736> ;
       bto:birthYear   ?by ;
       bto:hasEvent    ?ev .
  ?ev  a bto:Onset ;
       bto:ageOnset    ?ageOn ;
       bto:bulbarOnset ?b .
}
```

Generates a pseudonymous `?anonID` (SHA‑256 hash of the patient URI) with key clinical features.

PARAMETER(S): **none**

### **2. Anonymized treatment history for** DISEASE **patients:**

```sparql
SELECT (SHA256(STR(?pat))   AS ?anonID)
       ?tDate
       ?tType
WHERE {
  ?pat a bto:Patient ;
       bto:hasDisease     **<disease>** ;
       bto:hasEvent       ?ev .
  ?ev  a bto:TherapeuticProcedure ;
       bto:eventStart     ?tDate ;
       bto:procedureType  ?tType .
}
ORDER BY ?anonID ?tDate
```

Lists each anonymized patient’s timeline of therapeutic procedures (with dates & types).

PARAMETER(S): **`disease`**

---

## Level 6

### 1. All data for DISEASE patients (including identifiers):

```sparql
SELECT *
WHERE {
  ?pat a bto:Patient ;
       bto:hasDisease  **<disease>** ;
       ?p  ?o .
}
```

Retrieves every predicate‐object linked to each ALS patient (including personal identifiers).

PARAMETER(S): **`disease`**

### **2. Complete patient profiles for DISEASE (including name, DOB, sex, events, outcomes):**

```sparql
SELECT ?pat ?name ?birthYear ?sex ?ev ?evType ?evStart ?evEnd ?d
WHERE {
  ?pat a bto:Patient ;
       bto:hasName       ?name ;
       bto:birthYear     ?birthYear ;
       bto:sex           ?sex ;
       bto:hasEvent      ?ev ;
       bto:hasDisease    ?disease .
  ?ev  a ?evType ;
       bto:eventStart    ?evStart ;
       bto:eventEnd      ?evEnd .
  OPTIONAL { ?pat bto:deathDate ?d }
     FILTER (?disease = **<disease>**)
}

```

Pulls full demographic and event‐history details for each ALS patient, including identifiers and outcomes.

PARAMETER(S): **`disease`**

---

<aside>
📎

Nota: si possono modificare le query (indipendentemente dal livello) aggiungendo parametri di filtering, se l’obiettivo deve essere arricchire il form di interrogazione.

Nota: queries have been validated with [https://www.sparql.org/query-validator.html](https://www.sparql.org/query-validator.html)

</aside>

Useful Paper 

[200713 TRE Green Paper v2.0.pdf](200713_TRE_Green_Paper_v2.0.pdf)