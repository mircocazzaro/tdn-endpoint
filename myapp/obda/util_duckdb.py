#!/usr/bin/env python3
import re, sys

if len(sys.argv) != 3:
    print("Usage: lower_tables_stream.py <in.sql> <out.sql>")
    sys.exit(1)

infile, outfile = sys.argv[1], sys.argv[2]

# 1) First pass: collect all table names
tbl_map = {}
cre = re.compile(r'CREATE\s+TABLE\s+[`"]?([A-Za-z_]\w*)[`"]?', re.IGNORECASE)
with open(infile, 'r', encoding='utf8', errors='ignore') as f:
    for line in f:
        for match in cre.findall(line):
            tbl_map.setdefault(match, match.lower())

if not tbl_map:
    print("No tables found. Exiting.")
    sys.exit(0)

# Build a single regex that matches any of the table names as a whole word
# Sort keys by length descending so longer names get matched first
alts = sorted(tbl_map.keys(), key=len, reverse=True)
pattern = re.compile(r'\b(' + '|'.join(map(re.escape, alts)) + r')\b')

# 2) Second pass: stream input → output, replacing on the fly
with open(infile, 'r', encoding='utf8', errors='ignore') as src, \
     open(outfile, 'w', encoding='utf8') as dst:
    for line in src:
        dst.write(pattern.sub(lambda m: tbl_map[m.group(1)], line))

print(f"✅ Lowercased {len(tbl_map)} tables into “{outfile}”")
