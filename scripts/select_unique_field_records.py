#!/usr/bin/env python3
"""
Select one record per unique combination of raw metadata field values.

For each unique value-tuple across the requested fields, keeps the record
whose Assembly Accession (``genome.pairedAccession``, falling back to
``genome.accession``) sorts first alphabetically.

Field values are extracted with a coalesce strategy that checks BV-BRC,
biosample direct fields, and biosample attributes, mirroring the ingestion
logic in ``src/ontology_selection/ingest.py``.

Usage:
    # one record per unique (isolation_source, host)
    python scripts/select_unique_field_records.py \\
        -i dataset/curated_combined.jsonl \\
        -o dataset/curated_combined.unique_isolation_host.jsonl \\
        -f isolation_source host

    # one record per unique isolation_source only
    python scripts/select_unique_field_records.py \\
        -i dataset/curated_combined.jsonl \\
        -o dataset/curated_combined.unique_isolation.jsonl \\
        -f isolation_source
"""

import argparse
import json
import sys
from typing import Any, Optional


# -- field extraction --------------------------------------------------------

# Map of logical field name -> ordered list of paths to try.
# Each path is a tuple of (source, key) where source is one of:
#   "bv_brc"  -> bv_brc[0][key]
#   "bs"      -> biosample[0][key]
#   "attrs"   -> biosample[0].attributes flattened dict[key]
_FIELD_SOURCES: dict[str, list[tuple[str, str]]] = {
    "isolation_source": [
        ("bv_brc", "isolation_source"),
        ("attrs",  "isolation_source"),
        ("attrs",  "isolation source"),
    ],
    "host": [
        ("bs",     "host"),
        ("bv_brc", "host_name"),
        ("attrs",  "host"),
    ],
    "disease": [
        ("bv_brc", "disease"),
        ("attrs",  "disease"),
    ],
    "body_sample_site": [
        ("bv_brc", "body_sample_site"),
        ("attrs",  "body_sample_site"),
        ("attrs",  "body sample site"),
    ],
    "geo_loc_name": [
        ("bs",     "geoLocName"),
        ("bv_brc", "geographic_location"),
        ("attrs",  "geo_loc_name"),
    ],
    "strain": [
        ("bs",     "strain"),
        ("bv_brc", "strain"),
        ("attrs",  "strain"),
    ],
    "tissue": [
        ("attrs",  "tissue"),
    ],
    "environment": [
        ("attrs",  "env_medium"),
        ("attrs",  "environment"),
    ],
    "note": [
        ("attrs",  "note"),
    ],
    "collection_date": [
        ("bs",     "collectionDate"),
        ("bv_brc", "collection_date"),
        ("attrs",  "collection_date"),
    ],
}


def _coalesce_str(value: Any) -> str:
    """Normalise a field value to a string (empty string if missing)."""
    if value is None:
        return ""
    if isinstance(value, list):
        joined = "; ".join(str(x) for x in value if x)
        return joined
    s = str(value).strip()
    return s


def _extract_field(field: str, bs: dict, bvb: dict, attrs: dict) -> str:
    """Extract a logical field value using the coalesce lookup table."""
    sources = _FIELD_SOURCES.get(field)
    if sources is None:
        raise ValueError(
            f"Unknown field {field!r}. "
            f"Known fields: {', '.join(sorted(_FIELD_SOURCES))}"
        )
    for source, key in sources:
        if source == "bv_brc":
            v = bvb.get(key)
        elif source == "bs":
            v = bs.get(key)
        else:  # attrs
            v = attrs.get(key)
        s = _coalesce_str(v)
        if s:
            return s
    return ""


def _assembly_accession(record: dict) -> str:
    """Return the assembly accession used for sort-tiebreaking."""
    g = record.get("genome", {})
    return g.get("pairedAccession") or g.get("accession") or ""


# -- main logic --------------------------------------------------------------

def select_unique(input_path: str, output_path: str, fields: list[str]) -> tuple[int, int]:
    """Read *input_path*, keep one record per unique field-value tuple.

    Returns (total, kept).
    """
    # key -> (assembly_accession, original_line)
    best: dict[tuple[str, ...], tuple[str, str]] = {}
    total = 0

    with open(input_path) as f:
        for line in f:
            total += 1
            d = json.loads(line)

            # V2: biosamples (plural), V1: biosample (singular)
            bs = (d.get("biosamples") or d.get("biosample") or [{}])[0]

            # V2: bvbrc (dict), V1: bv_brc (list)
            bvb_raw = d.get("bvbrc") or d.get("bv_brc") or {}
            bvb = bvb_raw if isinstance(bvb_raw, dict) else (bvb_raw[0] if bvb_raw else {})

            # V2: attribute_recs [{attribute_name, harmonized_name, value}]
            # Key by harmonized_name (stable, normalised) when available.
            attr_recs = bs.get("attribute_recs", [])
            if attr_recs and isinstance(attr_recs[0], dict):
                attrs = {
                    (a.get("harmonized_name") or a.get("attribute_name") or ""): a.get("value", "")
                    for a in attr_recs
                    if (a.get("harmonized_name") or a.get("attribute_name"))
                }
            else:
                attrs = {
                    a["name"]: a["value"]
                    for a in bs.get("attributes", [])
                    if isinstance(a, dict) and "name" in a and "value" in a
                }

            key = tuple(_extract_field(fld, bs, bvb, attrs) for fld in fields)
            acc = _assembly_accession(d)

            prev = best.get(key)
            if prev is None or acc < prev[0]:
                best[key] = (acc, line)

    # Write output sorted by assembly accession for reproducibility
    with open(output_path, "w") as fout:
        for _acc, stored_line in sorted(best.values()):
            fout.write(stored_line)

    return total, len(best)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Select one record per unique combination of field values.",
    )
    parser.add_argument(
        "-i", "--input",
        default="dataset/curated_combined.jsonl",
        help="Input JSONL file (default: %(default)s)",
    )
    parser.add_argument(
        "-o", "--output",
        required=True,
        help="Output JSONL file",
    )
    parser.add_argument(
        "-f", "--fields",
        nargs="+",
        required=True,
        help=(
            "Field names to group by. Known fields: "
            + ", ".join(sorted(_FIELD_SOURCES))
        ),
    )
    args = parser.parse_args()

    total, kept = select_unique(args.input, args.output, args.fields)
    fields_str = ", ".join(args.fields)
    print(f"Selected {kept} unique ({fields_str}) records from {total} -> {args.output}")


if __name__ == "__main__":
    main()
