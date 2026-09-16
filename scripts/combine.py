#!/usr/bin/env python3

import argparse
import copy
from collections import defaultdict
import gzip
import json
from pathlib import Path

import yaml


def open_jsonl(path):
    path = Path(path)

    if path.name.endswith(".gz"):
        return gzip.open(path, "rt")

    return path.open("r")


# . means dive into but we need to encode DOT as a real dot.  yuck
def get_field(obj, field):
    for part in field.split("."):
        part = part.replace("DOT", ".")
        obj = obj[part]

    return obj


def elide_fields(obj, fields):
    obj = copy.deepcopy(obj)

    for field in fields:
        parts = [part.replace("DOT", ".") for part in field.split(".")]
        target = obj

        for part in parts[:-1]:
            try:
                target = target[part]
            except (KeyError, TypeError):
                target = None
                break

        if target is not None and isinstance(target, dict):
            target.pop(parts[-1], None)

    return obj


def output_record(name, obj, config):
    return elide_fields(obj, config.get("elide", []))


def load_index(name, config):
    """Load one JSONL file into a dictionary indexed by the configured field."""

    path = config["path"]
    if not Path(path).exists():
        raise ValueError(f"File for {name} does not exist: {path}")
    index_on = config["index_on"]


    print(f"Loading {name}: {path}", flush=True)
    print(f"  Indexing on: {index_on}", flush=True)

    index = {}

    records_without_field = 0
    with open_jsonl(path) as f:
        for line_number, line in enumerate(f, 1):
            if not line.strip():
                continue

            obj = json.loads(line)
            try:
                key = get_field(obj, index_on)
            except (KeyError, TypeError) as e:
                # print(e)
                records_without_field += 1
                continue

            index[key] = obj

    print(f"  Loaded {len(index):,} records", flush=True)
    print(f"  -> Records without field '{index_on}': {records_without_field:,}", flush=True)

    return index


def load_links(name, config):
    """Load tab-delimited links indexed by the primary record key."""

    path = Path(config["link_file"])
    if not path.exists():
        raise ValueError(f"Link file for {name} does not exist: {path}")
    related_column = config["column"] - 1

    if related_column < 1:
        raise ValueError(
            f"Column for {name} must be 2 or greater: {config['column']}"
        )

    print(f"Loading links for {name}: {path}", flush=True)
    print(f"  Related record column: {config['column']}", flush=True)

    links = defaultdict(list)

    with path.open() as f:
        for line_number, line in enumerate(f, 1):
            if not line.strip():
                continue

            fields = line.rstrip("\n").split("\t")

            if len(fields) <= related_column:
                raise ValueError(
                    f"Link file {path} line {line_number} has "
                    f"only {len(fields)} columns; expected column "
                    f"{config['column']}"
                )

            primary_key = fields[0]
            related_key = fields[related_column]
            links[primary_key].append(related_key)

    print(f"  Loaded {sum(map(len, links.values())):,} links", flush=True)

    return links


def main():
    parser = argparse.ArgumentParser(
        description="Combine related JSONL records into a single JSON object."
    )

    parser.add_argument(
        "config",
        type=Path,
        help="YAML configuration file",
    )

    parser.add_argument(
        "output",
        type=Path,
        help="Output JSONL file",
    )

    args = parser.parse_args()

    with args.config.open() as f:
        config = yaml.safe_load(f)

    primary = config["primary"]
    file_configs = config["files"]

    if primary not in file_configs:
        raise ValueError(
            f"Primary file '{primary}' is not defined in config"
        )

    for name, file_config in file_configs.items():
        column_related = file_config.get("column_related")

        if column_related is not None and column_related not in file_configs:
            raise ValueError(
                f"Related file '{column_related}' for {name} is not defined"
            )

        if name != primary and file_config.get("has_primary_key"):
            continue

        if name != primary and "link_file" not in file_config:
            raise ValueError(
                f"File '{name}' must define link_file or has_primary_key"
            )

    # Load every JSONL file into a dictionary.
    indexes = {}

    for name, file_config in file_configs.items():
        indexes[name] = load_index(name, file_config)

    # Load links from each related file to the primary file.
    link_indexes = {}

    for name, file_config in file_configs.items():
        if name != primary:
            if file_config.get("has_primary_key"):
                continue

            link_indexes[name] = load_links(name, file_config)

    # The primary index drives the output.
    primary_index = indexes[primary]

    print(
        f"\nCombining {len(primary_index):,} primary records...",
        flush=True,
    )

    total_records = len(primary_index)
    next_progress = 5

    with args.output.open("w") as out:
        for record_number, (key, primary_obj) in enumerate(
            primary_index.items(), 1
        ):

            combined = {
                primary: output_record(
                    primary, primary_obj, file_configs[primary]
                )
            }

            # Add all records linked to this primary record.
            for name in file_configs:
                if name == primary:
                    continue

                link_config = file_configs[name]
                if link_config.get("has_primary_key"):
                    primary_key_field = file_configs[primary].get("primary_key")
                    if primary_key_field is None:
                        raise ValueError(
                            f"Primary file '{primary}' must define primary_key "
                            f"for {name}"
                        )

                    try:
                        target_key = get_field(primary_obj, primary_key_field)
                    except (KeyError, TypeError):
                        target_key = None

                    linked_objects = []
                    if target_key in indexes[name]:
                        linked_objects.append(
                            output_record(
                                name,
                                indexes[name][target_key],
                                link_config,
                            )
                        )

                    if linked_objects:
                        combined[name] = linked_objects
                    continue

                link_keys = link_indexes[name].get(key, [])

                column_related = link_config.get("column_related")

                if column_related is not None:
                    related_keys = link_indexes[column_related].get(key, [])
                    link_keys = [
                        related_key
                        for related_key in related_keys
                        for related_key in link_indexes[name].get(
                            related_key, []
                        )
                    ]

                linked_objects = [
                    output_record(name, indexes[name][related_key], link_config)
                    for related_key in link_keys
                    if related_key in indexes[name]
                ]

                if linked_objects:
                    combined[name] = linked_objects

            out.write(json.dumps(combined, separators=(",", ":")) + "\n")

            if total_records:
                completed_percent = record_number * 100 // total_records
                while completed_percent >= next_progress:
                    print(f"  Progress: {next_progress}%", flush=True)
                    next_progress += 5

    print(f"Wrote {args.output}", flush=True)


if __name__ == "__main__":
    main()
