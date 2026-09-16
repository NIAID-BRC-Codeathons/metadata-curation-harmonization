import jsonlines
import requests

BASE_URL = "https://www.bv-brc.org/api/genome/"

QUERY = "eq(taxon_id,1280)"  # Staphylococcus aureus

page_size = 50
start = 0
page_number = 1
total_records = 0

with jsonlines.open("staph.jsonl", mode="w") as out:
    while True:

        url = (
            f"{BASE_URL}?{QUERY}"
            f"&limit({page_size},{start})"
            "&http_accept=application/json"
        )

        r = requests.get(url)
        r.raise_for_status()

        genomes = r.json()

        for genome in genomes:
            out.write(genome)

        total_records += len(genomes)
        print(
            f"Page {page_number}: wrote {len(genomes)} records "
            f"({total_records} total)",
            flush=True,
        )

        if len(genomes) < page_size:
            break

        start += page_size
        page_number += 1